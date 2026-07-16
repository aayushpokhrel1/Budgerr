from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.exceptions import ApiException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.betting_detection import is_betting_merchant
from app.deps import get_db
from app.models import Account, Category, PlaidItem, Transaction
from app.plaid_client import PlaidNotConfiguredError, get_plaid_client
from app.recurring import detect_recurring_charges
from app.routers.budgeting import recompute_budget_periods_for_month

router = APIRouter(prefix="/plaid", tags=["plaid"])

# Single-user personal app: one fixed Plaid end-user identity.
CLIENT_USER_ID = "budgerr-user-1"


class LinkTokenResponse(BaseModel):
    link_token: str


@router.post("/link-token", response_model=LinkTokenResponse)
def create_link_token() -> LinkTokenResponse:
    try:
        client = get_plaid_client()
    except PlaidNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    request = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id=CLIENT_USER_ID),
        client_name="Budgerr",
        products=[Products("transactions")],
        country_codes=[CountryCode("US")],
        language="en",
    )
    try:
        response = client.link_token_create(request)
    except ApiException as exc:
        raise HTTPException(status_code=502, detail=f"Plaid error: {exc.body}") from exc

    return LinkTokenResponse(link_token=response.link_token)


class ExchangePublicTokenRequest(BaseModel):
    public_token: str


class ExchangePublicTokenResponse(BaseModel):
    item_id: str
    accounts_created: int


@router.post("/exchange-public-token", response_model=ExchangePublicTokenResponse)
def exchange_public_token(
    body: ExchangePublicTokenRequest, db: Session = Depends(get_db)
) -> ExchangePublicTokenResponse:
    try:
        client = get_plaid_client()
    except PlaidNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        exchange_response = client.item_public_token_exchange(
            ItemPublicTokenExchangeRequest(public_token=body.public_token)
        )
    except ApiException as exc:
        raise HTTPException(status_code=502, detail=f"Plaid error: {exc.body}") from exc

    access_token = exchange_response.access_token
    item_id = exchange_response.item_id

    try:
        accounts_response = client.accounts_get(AccountsGetRequest(access_token=access_token))
    except ApiException as exc:
        raise HTTPException(status_code=502, detail=f"Plaid error: {exc.body}") from exc

    institution_id = accounts_response.item.institution_id
    institution_name = institution_id or "Unknown institution"
    if institution_id:
        try:
            institution_response = client.institutions_get_by_id(
                InstitutionsGetByIdRequest(
                    institution_id=institution_id, country_codes=[CountryCode("US")]
                )
            )
            institution_name = institution_response.institution.name
        except ApiException:
            pass  # fall back to the raw institution_id rather than failing the link

    plaid_item = db.get(PlaidItem, item_id)
    if plaid_item is None:
        plaid_item = PlaidItem(
            item_id=item_id, access_token=access_token, institution_name=institution_name
        )
        db.add(plaid_item)
    else:
        plaid_item.access_token = access_token

    accounts_created = 0
    for plaid_account in accounts_response.accounts:
        existing = (
            db.query(Account)
            .filter(Account.plaid_account_id == plaid_account.account_id)
            .one_or_none()
        )
        if existing is not None:
            existing.current_balance = plaid_account.balances.current or 0
            continue

        db.add(
            Account(
                plaid_item_id=item_id,
                plaid_account_id=plaid_account.account_id,
                institution_name=institution_name,
                account_type=str(plaid_account.type),
                mask=plaid_account.mask or "0000",
                current_balance=plaid_account.balances.current or 0,
            )
        )
        accounts_created += 1

    db.commit()

    return ExchangePublicTokenResponse(item_id=item_id, accounts_created=accounts_created)


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: int
    plaid_item_id: str
    institution_name: str
    account_type: str
    mask: str
    current_balance: float


@router.get("/accounts", response_model=list[AccountRead])
def list_accounts(db: Session = Depends(get_db)) -> list[Account]:
    return db.query(Account).order_by(Account.institution_name, Account.mask).all()


class SyncTransactionsResponse(BaseModel):
    added: int
    modified: int
    removed: int


def _plaid_category(plaid_txn) -> str | None:
    personal_finance_category = getattr(plaid_txn, "personal_finance_category", None)
    if personal_finance_category is not None:
        return personal_finance_category.primary
    category = getattr(plaid_txn, "category", None)
    return category[0] if category else None


@router.post("/sync-transactions/{item_id}", response_model=SyncTransactionsResponse)
def sync_transactions(item_id: str, db: Session = Depends(get_db)) -> SyncTransactionsResponse:
    plaid_item = db.get(PlaidItem, item_id)
    if plaid_item is None:
        raise HTTPException(status_code=404, detail=f"Unknown Plaid item_id: {item_id}")

    try:
        client = get_plaid_client()
    except PlaidNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    accounts_by_plaid_id = {
        a.plaid_account_id: a for a in db.query(Account).filter(Account.plaid_item_id == item_id)
    }
    # Best-effort auto-categorization: if Plaid's own category string happens
    # to match one of your budget categories by name, use it. Anything that
    # doesn't match stays uncategorized until set manually via PATCH
    # /transactions/{txn_id} — this never overrides a category you already set.
    categories_by_name = {c.name.strip().lower(): c.name for c in db.query(Category).all()}

    added_count = 0
    modified_count = 0
    removed_count = 0
    touched_months: set[date] = set()
    cursor = plaid_item.cursor
    has_more = True

    while has_more:
        request_kwargs = {"access_token": plaid_item.access_token}
        if cursor:
            request_kwargs["cursor"] = cursor

        try:
            response = client.transactions_sync(TransactionsSyncRequest(**request_kwargs))
        except ApiException as exc:
            raise HTTPException(status_code=502, detail=f"Plaid error: {exc.body}") from exc

        for plaid_txn in [*response.added, *response.modified]:
            account = accounts_by_plaid_id.get(plaid_txn.account_id)
            if account is None:
                continue

            existing = (
                db.query(Transaction)
                .filter(Transaction.plaid_transaction_id == plaid_txn.transaction_id)
                .one_or_none()
            )
            is_betting = is_betting_merchant(plaid_txn.merchant_name or plaid_txn.name)
            plaid_category = _plaid_category(plaid_txn)
            auto_category = (
                categories_by_name.get(plaid_category.strip().lower())
                if plaid_category and not is_betting
                else None
            )

            values = dict(
                account_id=account.account_id,
                plaid_transaction_id=plaid_txn.transaction_id,
                date=plaid_txn.date,
                amount=plaid_txn.amount,
                merchant_name=plaid_txn.merchant_name or plaid_txn.name,
                plaid_category=plaid_category,
                is_betting=is_betting,
            )
            touched_months.add(plaid_txn.date.replace(day=1))

            if existing is not None:
                for key, value in values.items():
                    setattr(existing, key, value)
                if existing.custom_category is None and auto_category:
                    existing.custom_category = auto_category
            else:
                db.add(Transaction(**values, custom_category=auto_category))

        for removed_txn in response.removed:
            db.query(Transaction).filter(
                Transaction.plaid_transaction_id == removed_txn.transaction_id
            ).delete()
            removed_count += 1

        added_count += len(response.added)
        modified_count += len(response.modified)
        cursor = response.next_cursor
        has_more = response.has_more

    plaid_item.cursor = cursor
    db.commit()

    for month in touched_months:
        recompute_budget_periods_for_month(db, month)

    return SyncTransactionsResponse(
        added=added_count, modified=modified_count, removed=removed_count
    )


class SyncAllResponse(BaseModel):
    synced: dict[str, SyncTransactionsResponse]
    errors: dict[str, str]


@router.post("/sync-all", response_model=SyncAllResponse)
def sync_all_transactions(db: Session = Depends(get_db)) -> SyncAllResponse:
    """Syncs every linked Plaid item, tolerating per-item failures — meant for
    the scheduled daily sync, where one broken item shouldn't block the rest.
    Budget-period recompute (and therefore alert-threshold checks) runs inside
    each item's sync.
    """
    synced: dict[str, SyncTransactionsResponse] = {}
    errors: dict[str, str] = {}
    for item in db.query(PlaidItem).all():
        try:
            synced[item.item_id] = sync_transactions(item.item_id, db)
        except HTTPException as exc:
            errors[item.item_id] = str(exc.detail)
    return SyncAllResponse(synced=synced, errors=errors)


class TransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    txn_id: int
    account_id: int
    date: date
    amount: float
    merchant_name: str | None
    plaid_category: str | None
    custom_category: str | None
    is_betting: bool


@router.get("/transactions", response_model=list[TransactionRead])
def list_transactions(
    start: date | None = None,
    end: date | None = None,
    account_id: int | None = None,
    uncategorized_only: bool = False,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[Transaction]:
    query = db.query(Transaction)
    if start is not None:
        query = query.filter(Transaction.date >= start)
    if end is not None:
        query = query.filter(Transaction.date < end)
    if account_id is not None:
        query = query.filter(Transaction.account_id == account_id)
    if uncategorized_only:
        query = query.filter(Transaction.custom_category.is_(None), Transaction.is_betting.is_(False))
    return query.order_by(Transaction.date.desc(), Transaction.txn_id.desc()).limit(limit).all()


class TransactionCategorize(BaseModel):
    custom_category: str | None


@router.patch("/transactions/{txn_id}", response_model=TransactionRead)
def categorize_transaction(
    txn_id: int, body: TransactionCategorize, db: Session = Depends(get_db)
) -> Transaction:
    txn = db.get(Transaction, txn_id)
    if txn is None:
        raise HTTPException(status_code=404, detail=f"No transaction with id {txn_id}")

    txn.custom_category = body.custom_category
    db.commit()
    db.refresh(txn)

    recompute_budget_periods_for_month(db, txn.date.replace(day=1))
    db.refresh(txn)
    return txn


class RecurringChargeRead(BaseModel):
    merchant_name: str
    last_amount: float
    avg_amount: float
    occurrences: int
    first_date: date
    last_date: date
    median_interval_days: float
    active: bool
    monthly_estimate: float
    cadence: str
    price_hiked: bool
    price_hike_amount: float | None
    price_hike_pct: float | None


class RecurringChargesResponse(BaseModel):
    recurring: list[RecurringChargeRead]
    monthly_total: float


@router.get("/recurring-charges", response_model=RecurringChargesResponse)
def recurring_charges(db: Session = Depends(get_db)) -> RecurringChargesResponse:
    """Detects subscription-like recurring charges by clustering transactions
    per merchant by amount, then checking for either a roughly monthly
    (20-40 day median, >=3 occurrences) or roughly annual (~365 +/- 30 day
    median, >=2 occurrences) cadence between them. Each cluster is also
    checked for a price hike (first-third vs last-third mean amount trending
    up past the clustering tolerance). See app/recurring.py for the pure
    detection logic.
    """
    transactions = db.query(Transaction).all()
    clusters = detect_recurring_charges(transactions)

    monthly_total = round(sum(c.monthly_estimate for c in clusters if c.active), 2)

    return RecurringChargesResponse(
        recurring=[
            RecurringChargeRead(
                merchant_name=c.merchant_name,
                last_amount=c.last_amount,
                avg_amount=c.avg_amount,
                occurrences=c.occurrences,
                first_date=c.first_date,
                last_date=c.last_date,
                median_interval_days=c.median_interval_days,
                active=c.active,
                monthly_estimate=c.monthly_estimate,
                cadence=c.cadence,
                price_hiked=c.price_hiked,
                price_hike_amount=c.price_hike_amount,
                price_hike_pct=c.price_hike_pct,
            )
            for c in clusters
        ],
        monthly_total=monthly_total,
    )
