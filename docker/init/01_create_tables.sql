-- =============================================================================
-- KYC Schema DDL
-- Runs as APP_USER (kyc) inside FREEPDB1
-- =============================================================================

-- ── 1. EMPLOYEES (no FKs — must be first) ────────────────────────────────────
CREATE TABLE employees (
    employee_id   NUMBER(10)   NOT NULL,
    first_name    VARCHAR2(100) NOT NULL,
    last_name     VARCHAR2(100) NOT NULL,
    department    VARCHAR2(100),
    role          VARCHAR2(100),
    email         VARCHAR2(200),
    CONSTRAINT pk_employees PRIMARY KEY (employee_id)
);

-- ── 2. CUSTOMERS ─────────────────────────────────────────────────────────────
CREATE TABLE customers (
    customer_id        NUMBER(10)   NOT NULL,
    first_name         VARCHAR2(100) NOT NULL,
    last_name          VARCHAR2(100) NOT NULL,
    date_of_birth      DATE,
    nationality        VARCHAR2(3),
    ssn                VARCHAR2(20),
    passport_no        VARCHAR2(30),
    risk_rating        VARCHAR2(10)  NOT NULL,
    account_manager_id NUMBER(10),
    created_date       DATE         NOT NULL,
    CONSTRAINT pk_customers PRIMARY KEY (customer_id),
    CONSTRAINT chk_risk_rating CHECK (risk_rating IN ('LOW','MEDIUM','HIGH','VERY_HIGH'))
);

-- ── 3. ACCOUNTS ──────────────────────────────────────────────────────────────
CREATE TABLE accounts (
    account_id   NUMBER(12)    NOT NULL,
    customer_id  NUMBER(10)    NOT NULL,
    account_type VARCHAR2(20)  NOT NULL,
    balance      NUMBER(18,2)  NOT NULL,
    currency     VARCHAR2(3)   NOT NULL,
    status       VARCHAR2(20)  NOT NULL,
    opened_date  DATE          NOT NULL,
    CONSTRAINT pk_accounts PRIMARY KEY (account_id),
    CONSTRAINT chk_account_type CHECK (account_type IN ('SAVINGS','CURRENT','INVESTMENT')),
    CONSTRAINT chk_account_status CHECK (status IN ('ACTIVE','DORMANT','CLOSED','FROZEN'))
);

-- ── 4. TRANSACTIONS ───────────────────────────────────────────────────────────
CREATE TABLE transactions (
    transaction_id   NUMBER(15)   NOT NULL,
    account_id       NUMBER(12)   NOT NULL,
    amount           NUMBER(18,2) NOT NULL,
    currency         VARCHAR2(3)  NOT NULL,
    transaction_date DATE         NOT NULL,
    description      VARCHAR2(500),
    transaction_type VARCHAR2(30) NOT NULL,
    is_flagged       CHAR(1)      NOT NULL,
    CONSTRAINT pk_transactions PRIMARY KEY (transaction_id),
    CONSTRAINT chk_txn_type CHECK (transaction_type IN ('DEBIT','CREDIT','WIRE','INTERNAL')),
    CONSTRAINT chk_is_flagged CHECK (is_flagged IN ('Y','N'))
);

-- ── 5. KYC_REVIEWS ────────────────────────────────────────────────────────────
CREATE TABLE kyc_reviews (
    review_id        NUMBER(12)  NOT NULL,
    customer_id      NUMBER(10)  NOT NULL,
    review_date      DATE        NOT NULL,
    reviewer_id      NUMBER(10)  NOT NULL,
    status           VARCHAR2(20) NOT NULL,
    next_review_date DATE,
    notes            CLOB,
    CONSTRAINT pk_kyc_reviews PRIMARY KEY (review_id),
    CONSTRAINT chk_review_status CHECK (status IN ('PENDING','COMPLETED','FAILED','ESCALATED'))
);

-- ── 6. RISK_ASSESSMENTS ───────────────────────────────────────────────────────
CREATE TABLE risk_assessments (
    assessment_id NUMBER(12)  NOT NULL,
    customer_id   NUMBER(10)  NOT NULL,
    risk_score    NUMBER(5,2) NOT NULL,
    risk_level    VARCHAR2(10) NOT NULL,
    assessed_date DATE        NOT NULL,
    assessed_by   NUMBER(10),
    CONSTRAINT pk_risk_assessments PRIMARY KEY (assessment_id),
    CONSTRAINT chk_risk_level CHECK (risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH'))
);

-- ── 7. BENEFICIAL_OWNERS ─────────────────────────────────────────────────────
CREATE TABLE beneficial_owners (
    owner_id      NUMBER(12)   NOT NULL,
    customer_id   NUMBER(10)   NOT NULL,
    owner_name    VARCHAR2(200) NOT NULL,
    ownership_pct NUMBER(5,2)  NOT NULL,
    relationship  VARCHAR2(50) NOT NULL,
    CONSTRAINT pk_beneficial_owners PRIMARY KEY (owner_id),
    CONSTRAINT chk_ownership_pct CHECK (ownership_pct BETWEEN 0 AND 100)
);

-- ── 8. PEP_STATUS ─────────────────────────────────────────────────────────────
CREATE TABLE pep_status (
    pep_id      NUMBER(12) NOT NULL,
    customer_id NUMBER(10) NOT NULL,
    is_pep      CHAR(1)    NOT NULL,
    pep_type    VARCHAR2(50),
    listed_date DATE,
    CONSTRAINT pk_pep_status PRIMARY KEY (pep_id),
    CONSTRAINT chk_is_pep CHECK (is_pep IN ('Y','N'))
);

-- =============================================================================
-- Foreign Key Constraints
-- =============================================================================
ALTER TABLE customers
    ADD CONSTRAINT fk_cust_manager
    FOREIGN KEY (account_manager_id) REFERENCES employees (employee_id);

ALTER TABLE accounts
    ADD CONSTRAINT fk_accounts_customer
    FOREIGN KEY (customer_id) REFERENCES customers (customer_id);

ALTER TABLE transactions
    ADD CONSTRAINT fk_transactions_account
    FOREIGN KEY (account_id) REFERENCES accounts (account_id);

ALTER TABLE kyc_reviews
    ADD CONSTRAINT fk_reviews_customer
    FOREIGN KEY (customer_id) REFERENCES customers (customer_id);

ALTER TABLE kyc_reviews
    ADD CONSTRAINT fk_reviews_reviewer
    FOREIGN KEY (reviewer_id) REFERENCES employees (employee_id);

ALTER TABLE risk_assessments
    ADD CONSTRAINT fk_risk_customer
    FOREIGN KEY (customer_id) REFERENCES customers (customer_id);

ALTER TABLE risk_assessments
    ADD CONSTRAINT fk_risk_assessor
    FOREIGN KEY (assessed_by) REFERENCES employees (employee_id);

ALTER TABLE beneficial_owners
    ADD CONSTRAINT fk_bene_customer
    FOREIGN KEY (customer_id) REFERENCES customers (customer_id);

ALTER TABLE pep_status
    ADD CONSTRAINT fk_pep_customer
    FOREIGN KEY (customer_id) REFERENCES customers (customer_id);

-- =============================================================================
-- Indexes
-- =============================================================================
CREATE INDEX idx_cust_risk    ON customers        (risk_rating);
CREATE INDEX idx_cust_mgr     ON customers        (account_manager_id);
CREATE INDEX idx_acct_cust    ON accounts         (customer_id);
CREATE INDEX idx_txn_acct     ON transactions     (account_id);
CREATE INDEX idx_txn_date     ON transactions     (transaction_date);
CREATE INDEX idx_kyc_cust     ON kyc_reviews      (customer_id);
CREATE INDEX idx_kyc_date     ON kyc_reviews      (review_date);
CREATE INDEX idx_risk_cust    ON risk_assessments (customer_id);
CREATE INDEX idx_bene_cust    ON beneficial_owners(customer_id);
CREATE INDEX idx_pep_cust     ON pep_status       (customer_id);

-- =============================================================================
-- Table & Column Comments
-- =============================================================================
COMMENT ON TABLE employees         IS 'Employee directory including account managers and reviewers';
COMMENT ON TABLE customers         IS 'Core customer entity for KYC compliance';
COMMENT ON TABLE accounts          IS 'Customer accounts';
COMMENT ON TABLE transactions      IS 'Financial transactions';
COMMENT ON TABLE kyc_reviews       IS 'Periodic KYC review records';
COMMENT ON TABLE risk_assessments  IS 'Customer risk scores';
COMMENT ON TABLE beneficial_owners IS 'Ultimate beneficial owner records';
COMMENT ON TABLE pep_status        IS 'Politically exposed person flags';

-- CUSTOMERS columns
COMMENT ON COLUMN customers.customer_id        IS 'Unique customer identifier';
COMMENT ON COLUMN customers.risk_rating        IS 'Risk level: LOW | MEDIUM | HIGH | VERY_HIGH';
COMMENT ON COLUMN customers.nationality        IS 'ISO 3166-1 alpha-3 country code';
COMMENT ON COLUMN customers.ssn                IS 'Social security number (masked)';
COMMENT ON COLUMN customers.passport_no        IS 'Passport number (masked)';
COMMENT ON COLUMN customers.account_manager_id IS 'FK to EMPLOYEES.EMPLOYEE_ID';
COMMENT ON COLUMN customers.created_date       IS 'Customer onboarding date';

-- ACCOUNTS columns
COMMENT ON COLUMN accounts.account_type IS 'SAVINGS | CURRENT | INVESTMENT';
COMMENT ON COLUMN accounts.balance      IS 'Current account balance';
COMMENT ON COLUMN accounts.currency     IS 'ISO 4217 currency code';
COMMENT ON COLUMN accounts.status       IS 'ACTIVE | DORMANT | CLOSED | FROZEN';
COMMENT ON COLUMN accounts.customer_id  IS 'FK to CUSTOMERS.CUSTOMER_ID';

-- TRANSACTIONS columns
COMMENT ON COLUMN transactions.transaction_type IS 'DEBIT | CREDIT | WIRE | INTERNAL';
COMMENT ON COLUMN transactions.is_flagged       IS 'Y = flagged for AML investigation';
COMMENT ON COLUMN transactions.account_id       IS 'FK to ACCOUNTS.ACCOUNT_ID';

-- KYC_REVIEWS columns
COMMENT ON COLUMN kyc_reviews.status      IS 'PENDING | COMPLETED | FAILED | ESCALATED';
COMMENT ON COLUMN kyc_reviews.customer_id IS 'FK to CUSTOMERS.CUSTOMER_ID';
COMMENT ON COLUMN kyc_reviews.reviewer_id IS 'FK to EMPLOYEES.EMPLOYEE_ID';

-- RISK_ASSESSMENTS columns
COMMENT ON COLUMN risk_assessments.risk_score  IS 'Composite risk score 0-100';
COMMENT ON COLUMN risk_assessments.risk_level  IS 'LOW | MEDIUM | HIGH | VERY_HIGH';
COMMENT ON COLUMN risk_assessments.customer_id IS 'FK to CUSTOMERS.CUSTOMER_ID';

-- BENEFICIAL_OWNERS columns
COMMENT ON COLUMN beneficial_owners.ownership_pct IS 'Ownership percentage (0-100)';
COMMENT ON COLUMN beneficial_owners.customer_id   IS 'FK to CUSTOMERS.CUSTOMER_ID';

-- PEP_STATUS columns
COMMENT ON COLUMN pep_status.is_pep      IS 'Y | N';
COMMENT ON COLUMN pep_status.pep_type    IS 'HEAD_OF_STATE | SENIOR_OFFICIAL | JUDGE | MILITARY';
COMMENT ON COLUMN pep_status.customer_id IS 'FK to CUSTOMERS.CUSTOMER_ID';
