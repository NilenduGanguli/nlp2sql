-- =============================================================================
-- KYC Sample Data
-- Runs as APP_USER (kyc) inside FREEPDB1
-- =============================================================================

-- ── EMPLOYEES (account managers and reviewers) ─────────────────────────────
INSERT INTO employees VALUES (1, 'Sarah',   'Thompson', 'Compliance', 'Senior Analyst',    'sarah.thompson@bank.com');
INSERT INTO employees VALUES (2, 'Marcus',  'Chen',     'Risk',       'Risk Manager',       'marcus.chen@bank.com');
INSERT INTO employees VALUES (3, 'Emily',   'Rodriguez','Compliance', 'KYC Analyst',        'emily.rodriguez@bank.com');
INSERT INTO employees VALUES (4, 'James',   'Wilson',   'Operations', 'Account Manager',    'james.wilson@bank.com');
INSERT INTO employees VALUES (5, 'Priya',   'Patel',    'Compliance', 'Director',           'priya.patel@bank.com');
INSERT INTO employees VALUES (6, 'Daniel',  'Kim',      'Risk',       'Analyst',            'daniel.kim@bank.com');
INSERT INTO employees VALUES (7, 'Claire',  'Dubois',   'IT',         'Senior Analyst',     'claire.dubois@bank.com');
INSERT INTO employees VALUES (8, 'Robert',  'Okonkwo',  'Finance',    'Associate',          'robert.okonkwo@bank.com');

-- ── CUSTOMERS (mix of risk levels, nationalities) ──────────────────────────
INSERT INTO customers VALUES (1001, 'Alice',   'Hartmann',  TO_DATE('1978-03-22','YYYY-MM-DD'), 'DEU', NULL,         'P123456DE', 'LOW',       4, TO_DATE('2019-06-15','YYYY-MM-DD'));
INSERT INTO customers VALUES (1002, 'Boris',   'Volkov',    TO_DATE('1965-11-08','YYYY-MM-DD'), 'RUS', NULL,         NULL,         'HIGH',      2, TO_DATE('2020-01-30','YYYY-MM-DD'));
INSERT INTO customers VALUES (1003, 'Chen',    'Wei',       TO_DATE('1990-07-14','YYYY-MM-DD'), 'CHN', NULL,         'P789012CN', 'MEDIUM',    4, TO_DATE('2021-03-10','YYYY-MM-DD'));
INSERT INTO customers VALUES (1004, 'Diana',   'Osei',      TO_DATE('1984-05-19','YYYY-MM-DD'), 'GHA', 'SSN-404',    NULL,         'LOW',       4, TO_DATE('2018-11-20','YYYY-MM-DD'));
INSERT INTO customers VALUES (1005, 'Eduardo', 'Ferreira',  TO_DATE('1972-09-30','YYYY-MM-DD'), 'BRA', NULL,         'P345678BR', 'MEDIUM',    4, TO_DATE('2022-05-01','YYYY-MM-DD'));
INSERT INTO customers VALUES (1006, 'Fatima',  'Al-Rashid', TO_DATE('1969-02-14','YYYY-MM-DD'), 'ARE', NULL,         'P901234AE', 'VERY_HIGH', 2, TO_DATE('2020-09-15','YYYY-MM-DD'));
INSERT INTO customers VALUES (1007, 'George',  'Papadopoulos',TO_DATE('1955-12-01','YYYY-MM-DD'),'GRC',NULL,         'P567890GR', 'LOW',       4, TO_DATE('2017-04-22','YYYY-MM-DD'));
INSERT INTO customers VALUES (1008, 'Helen',   'Nakamura',  TO_DATE('1988-08-25','YYYY-MM-DD'), 'JPN', NULL,         'P678901JP', 'LOW',       4, TO_DATE('2023-01-05','YYYY-MM-DD'));
INSERT INTO customers VALUES (1009, 'Ivan',    'Petrov',    TO_DATE('1975-04-11','YYYY-MM-DD'), 'BGR', NULL,         NULL,         'HIGH',      2, TO_DATE('2021-07-19','YYYY-MM-DD'));
INSERT INTO customers VALUES (1010, 'Julia',   'Santos',    TO_DATE('1993-10-28','YYYY-MM-DD'), 'BRA', 'SSN-020',    NULL,         'LOW',       4, TO_DATE('2022-11-11','YYYY-MM-DD'));
INSERT INTO customers VALUES (1011, 'Kwame',   'Mensah',    TO_DATE('1980-06-03','YYYY-MM-DD'), 'GHA', NULL,         'P111222GH', 'MEDIUM',    2, TO_DATE('2019-12-01','YYYY-MM-DD'));
INSERT INTO customers VALUES (1012, 'Leila',   'Hosseini',  TO_DATE('1967-01-17','YYYY-MM-DD'), 'IRN', NULL,         NULL,         'VERY_HIGH', 2, TO_DATE('2020-03-25','YYYY-MM-DD'));
INSERT INTO customers VALUES (1013, 'Michael', 'Brown',     TO_DATE('1995-09-09','YYYY-MM-DD'), 'USA', 'SSN-130',    NULL,         'LOW',       4, TO_DATE('2023-03-12','YYYY-MM-DD'));
INSERT INTO customers VALUES (1014, 'Nadia',   'Kovacs',    TO_DATE('1982-11-22','YYYY-MM-DD'), 'HUN', NULL,         'P444555HU', 'MEDIUM',    4, TO_DATE('2021-08-30','YYYY-MM-DD'));
INSERT INTO customers VALUES (1015, 'Omar',    'Abdullah',  TO_DATE('1970-03-05','YYYY-MM-DD'), 'SAU', NULL,         'P666777SA', 'HIGH',      2, TO_DATE('2020-06-14','YYYY-MM-DD'));

-- ── ACCOUNTS ──────────────────────────────────────────────────────────────────
INSERT INTO accounts VALUES (2001, 1001, 'SAVINGS',    125000.00, 'EUR', 'ACTIVE',  TO_DATE('2019-06-15','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2002, 1001, 'INVESTMENT',  50000.00, 'USD', 'ACTIVE',  TO_DATE('2020-02-10','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2003, 1002, 'CURRENT',    380000.00, 'USD', 'ACTIVE',  TO_DATE('2020-01-30','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2004, 1003, 'SAVINGS',     75000.00, 'CNY', 'ACTIVE',  TO_DATE('2021-03-10','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2005, 1004, 'SAVINGS',     28000.00, 'USD', 'ACTIVE',  TO_DATE('2018-11-20','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2006, 1005, 'CURRENT',    220000.00, 'BRL', 'ACTIVE',  TO_DATE('2022-05-01','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2007, 1006, 'INVESTMENT', 950000.00, 'USD', 'FROZEN',  TO_DATE('2020-09-15','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2008, 1007, 'SAVINGS',    310000.00, 'EUR', 'ACTIVE',  TO_DATE('2017-04-22','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2009, 1008, 'SAVINGS',     42000.00, 'JPY', 'ACTIVE',  TO_DATE('2023-01-05','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2010, 1009, 'CURRENT',    195000.00, 'USD', 'DORMANT', TO_DATE('2021-07-19','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2011, 1010, 'SAVINGS',     18000.00, 'BRL', 'ACTIVE',  TO_DATE('2022-11-11','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2012, 1011, 'CURRENT',    450000.00, 'USD', 'ACTIVE',  TO_DATE('2019-12-01','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2013, 1012, 'INVESTMENT', 720000.00, 'USD', 'FROZEN',  TO_DATE('2020-03-25','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2014, 1013, 'SAVINGS',     55000.00, 'USD', 'ACTIVE',  TO_DATE('2023-03-12','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2015, 1014, 'CURRENT',    130000.00, 'EUR', 'ACTIVE',  TO_DATE('2021-08-30','YYYY-MM-DD'));
INSERT INTO accounts VALUES (2016, 1015, 'INVESTMENT', 880000.00, 'USD', 'ACTIVE',  TO_DATE('2020-06-14','YYYY-MM-DD'));

-- ── TRANSACTIONS ──────────────────────────────────────────────────────────────
INSERT INTO transactions VALUES (3001, 2003, 250000.00, 'USD', TO_DATE('2024-01-10','YYYY-MM-DD'), 'International wire transfer',       'WIRE',     'Y');
INSERT INTO transactions VALUES (3002, 2003,  85000.00, 'USD', TO_DATE('2024-01-15','YYYY-MM-DD'), 'Wire to offshore account',          'WIRE',     'Y');
INSERT INTO transactions VALUES (3003, 2001,   1200.00, 'EUR', TO_DATE('2024-01-20','YYYY-MM-DD'), 'Monthly salary deposit',            'CREDIT',   'N');
INSERT INTO transactions VALUES (3004, 2001,    450.00, 'EUR', TO_DATE('2024-01-22','YYYY-MM-DD'), 'Groceries payment',                 'DEBIT',    'N');
INSERT INTO transactions VALUES (3005, 2007, 500000.00, 'USD', TO_DATE('2024-01-05','YYYY-MM-DD'), 'Large cash deposit — unexplained',  'CREDIT',   'Y');
INSERT INTO transactions VALUES (3006, 2007, 490000.00, 'USD', TO_DATE('2024-01-06','YYYY-MM-DD'), 'Immediate withdrawal after deposit','DEBIT',    'Y');
INSERT INTO transactions VALUES (3007, 2008,   8500.00, 'EUR', TO_DATE('2024-01-18','YYYY-MM-DD'), 'Quarterly dividends',               'CREDIT',   'N');
INSERT INTO transactions VALUES (3008, 2008,   3200.00, 'EUR', TO_DATE('2024-01-25','YYYY-MM-DD'), 'Investment rebalancing',            'INTERNAL', 'N');
INSERT INTO transactions VALUES (3009, 2013, 300000.00, 'USD', TO_DATE('2024-01-12','YYYY-MM-DD'), 'Property purchase advance',         'DEBIT',    'Y');
INSERT INTO transactions VALUES (3010, 2004,   5000.00, 'CNY', TO_DATE('2024-01-30','YYYY-MM-DD'), 'Rent payment',                      'DEBIT',    'N');
INSERT INTO transactions VALUES (3011, 2010, 150000.00, 'USD', TO_DATE('2024-02-01','YYYY-MM-DD'), 'Business transfer',                 'WIRE',     'Y');
INSERT INTO transactions VALUES (3012, 2012, 420000.00, 'USD', TO_DATE('2024-02-03','YYYY-MM-DD'), 'Corporate receipts — multiple',     'CREDIT',   'Y');
INSERT INTO transactions VALUES (3013, 2012,  15000.00, 'USD', TO_DATE('2024-02-05','YYYY-MM-DD'), 'Legal fees payment',                'WIRE',     'N');
INSERT INTO transactions VALUES (3014, 2005,    800.00, 'USD', TO_DATE('2024-02-07','YYYY-MM-DD'), 'Utility bills',                     'DEBIT',    'N');
INSERT INTO transactions VALUES (3015, 2016, 200000.00, 'USD', TO_DATE('2024-02-10','YYYY-MM-DD'), 'Investment fund redemption',        'CREDIT',   'N');
INSERT INTO transactions VALUES (3016, 2016, 195000.00, 'USD', TO_DATE('2024-02-11','YYYY-MM-DD'), 'Reinvestment',                      'DEBIT',    'N');
INSERT INTO transactions VALUES (3017, 2009,  12000.00, 'JPY', TO_DATE('2024-02-14','YYYY-MM-DD'), 'Monthly salary',                    'CREDIT',   'N');
INSERT INTO transactions VALUES (3018, 2011,    600.00, 'BRL', TO_DATE('2024-02-20','YYYY-MM-DD'), 'Online shopping',                   'DEBIT',    'N');
INSERT INTO transactions VALUES (3019, 2006,  80000.00, 'BRL', TO_DATE('2024-02-22','YYYY-MM-DD'), 'Commercial property payment',       'WIRE',     'N');
INSERT INTO transactions VALUES (3020, 2015, 110000.00, 'EUR', TO_DATE('2024-02-25','YYYY-MM-DD'), 'Business account incoming',          'CREDIT',   'N');

-- ── KYC_REVIEWS ───────────────────────────────────────────────────────────────
INSERT INTO kyc_reviews VALUES (4001, 1001, TO_DATE('2024-01-15','YYYY-MM-DD'), 3, 'COMPLETED', TO_DATE('2025-01-15','YYYY-MM-DD'), 'Annual review — no issues');
INSERT INTO kyc_reviews VALUES (4002, 1002, TO_DATE('2024-01-20','YYYY-MM-DD'), 1, 'ESCALATED', TO_DATE('2024-07-20','YYYY-MM-DD'), 'Significant wire activity — escalated to compliance director');
INSERT INTO kyc_reviews VALUES (4003, 1003, TO_DATE('2024-02-01','YYYY-MM-DD'), 3, 'COMPLETED', TO_DATE('2025-02-01','YYYY-MM-DD'), 'Standard annual review passed');
INSERT INTO kyc_reviews VALUES (4004, 1004, TO_DATE('2024-02-10','YYYY-MM-DD'), 3, 'COMPLETED', TO_DATE('2025-02-10','YYYY-MM-DD'), 'CDD complete — low risk confirmed');
INSERT INTO kyc_reviews VALUES (4005, 1005, TO_DATE('2024-02-15','YYYY-MM-DD'), 1, 'PENDING',   NULL,                                NULL);
INSERT INTO kyc_reviews VALUES (4006, 1006, TO_DATE('2024-01-08','YYYY-MM-DD'), 5, 'FAILED',    TO_DATE('2024-04-08','YYYY-MM-DD'), 'Source of funds unverified — enhanced due diligence required');
INSERT INTO kyc_reviews VALUES (4007, 1007, TO_DATE('2024-03-01','YYYY-MM-DD'), 3, 'COMPLETED', TO_DATE('2025-03-01','YYYY-MM-DD'), 'Long-standing customer — clean record');
INSERT INTO kyc_reviews VALUES (4008, 1008, TO_DATE('2024-03-05','YYYY-MM-DD'), 6, 'COMPLETED', TO_DATE('2025-03-05','YYYY-MM-DD'), 'New customer onboarding review passed');
INSERT INTO kyc_reviews VALUES (4009, 1009, TO_DATE('2024-01-25','YYYY-MM-DD'), 1, 'ESCALATED', TO_DATE('2024-07-25','YYYY-MM-DD'), 'Dormant account reactivated with large transfer — suspicious');
INSERT INTO kyc_reviews VALUES (4010, 1010, TO_DATE('2024-03-10','YYYY-MM-DD'), 6, 'COMPLETED', TO_DATE('2025-03-10','YYYY-MM-DD'), 'EDD complete — low risk confirmed');
INSERT INTO kyc_reviews VALUES (4011, 1011, TO_DATE('2024-02-20','YYYY-MM-DD'), 1, 'COMPLETED', TO_DATE('2025-02-20','YYYY-MM-DD'), 'Medium risk — enhanced monitoring applied');
INSERT INTO kyc_reviews VALUES (4012, 1012, TO_DATE('2024-01-30','YYYY-MM-DD'), 5, 'FAILED',    TO_DATE('2024-04-30','YYYY-MM-DD'), 'PEP — very high risk profile. Full EDD required');
INSERT INTO kyc_reviews VALUES (4013, 1013, TO_DATE('2024-03-15','YYYY-MM-DD'), 3, 'COMPLETED', TO_DATE('2025-03-15','YYYY-MM-DD'), 'Initial onboarding KYC complete');
INSERT INTO kyc_reviews VALUES (4014, 1014, TO_DATE('2024-03-20','YYYY-MM-DD'), 6, 'PENDING',   NULL,                                NULL);
INSERT INTO kyc_reviews VALUES (4015, 1015, TO_DATE('2024-02-28','YYYY-MM-DD'), 1, 'ESCALATED', TO_DATE('2024-08-28','YYYY-MM-DD'), 'High-risk jurisdiction — enhanced review');

-- ── RISK_ASSESSMENTS ──────────────────────────────────────────────────────────
INSERT INTO risk_assessments VALUES (5001, 1001, 18.50, 'LOW',       TO_DATE('2024-01-15','YYYY-MM-DD'), 3);
INSERT INTO risk_assessments VALUES (5002, 1002, 78.25, 'HIGH',      TO_DATE('2024-01-20','YYYY-MM-DD'), 2);
INSERT INTO risk_assessments VALUES (5003, 1003, 42.00, 'MEDIUM',    TO_DATE('2024-02-01','YYYY-MM-DD'), 6);
INSERT INTO risk_assessments VALUES (5004, 1004, 12.75, 'LOW',       TO_DATE('2024-02-10','YYYY-MM-DD'), 3);
INSERT INTO risk_assessments VALUES (5005, 1005, 55.00, 'MEDIUM',    TO_DATE('2024-02-15','YYYY-MM-DD'), 6);
INSERT INTO risk_assessments VALUES (5006, 1006, 95.50, 'VERY_HIGH', TO_DATE('2024-01-08','YYYY-MM-DD'), 5);
INSERT INTO risk_assessments VALUES (5007, 1007, 10.00, 'LOW',       TO_DATE('2024-03-01','YYYY-MM-DD'), 3);
INSERT INTO risk_assessments VALUES (5008, 1008, 15.25, 'LOW',       TO_DATE('2024-03-05','YYYY-MM-DD'), 6);
INSERT INTO risk_assessments VALUES (5009, 1009, 72.00, 'HIGH',      TO_DATE('2024-01-25','YYYY-MM-DD'), 2);
INSERT INTO risk_assessments VALUES (5010, 1010, 22.00, 'LOW',       TO_DATE('2024-03-10','YYYY-MM-DD'), 3);
INSERT INTO risk_assessments VALUES (5011, 1011, 48.00, 'MEDIUM',    TO_DATE('2024-02-20','YYYY-MM-DD'), 2);
INSERT INTO risk_assessments VALUES (5012, 1012, 97.00, 'VERY_HIGH', TO_DATE('2024-01-30','YYYY-MM-DD'), 5);
INSERT INTO risk_assessments VALUES (5013, 1013, 14.00, 'LOW',       TO_DATE('2024-03-15','YYYY-MM-DD'), 6);
INSERT INTO risk_assessments VALUES (5014, 1014, 38.50, 'MEDIUM',    TO_DATE('2024-03-20','YYYY-MM-DD'), 6);
INSERT INTO risk_assessments VALUES (5015, 1015, 69.00, 'HIGH',      TO_DATE('2024-02-28','YYYY-MM-DD'), 2);

-- ── BENEFICIAL_OWNERS ─────────────────────────────────────────────────────────
INSERT INTO beneficial_owners VALUES (6001, 1002, 'Boris Volkov Holdings Ltd', 100.00, 'Director');
INSERT INTO beneficial_owners VALUES (6002, 1006, 'Al-Rashid Family Trust',     75.00, 'Beneficiary');
INSERT INTO beneficial_owners VALUES (6003, 1006, 'Gulf Investments LLC',        25.00, 'Shareholder');
INSERT INTO beneficial_owners VALUES (6004, 1009, 'Eastern European Capital',   60.00, 'Shareholder');
INSERT INTO beneficial_owners VALUES (6005, 1009, 'Ivan Petrov (personal)',      40.00, 'Director');
INSERT INTO beneficial_owners VALUES (6006, 1011, 'West Africa Ventures',       51.00, 'Shareholder');
INSERT INTO beneficial_owners VALUES (6007, 1012, 'Hosseini Group DMCC',        80.00, 'Director');
INSERT INTO beneficial_owners VALUES (6008, 1015, 'Omar Abdullah Holding Co',  100.00, 'Director');

-- ── PEP_STATUS ────────────────────────────────────────────────────────────────
INSERT INTO pep_status VALUES (7001, 1002, 'N', NULL,             NULL);
INSERT INTO pep_status VALUES (7002, 1006, 'Y', 'SENIOR_OFFICIAL', TO_DATE('2018-06-01','YYYY-MM-DD'));
INSERT INTO pep_status VALUES (7003, 1009, 'N', NULL,             NULL);
INSERT INTO pep_status VALUES (7004, 1012, 'Y', 'JUDGE',          TO_DATE('2015-03-15','YYYY-MM-DD'));
INSERT INTO pep_status VALUES (7005, 1015, 'Y', 'SENIOR_OFFICIAL', TO_DATE('2019-11-20','YYYY-MM-DD'));

COMMIT;
