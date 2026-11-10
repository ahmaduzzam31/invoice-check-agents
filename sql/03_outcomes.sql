-- What happened to the 60 invoices: approved without a person, rightly held, or held by mistake.
-- All data is SIMULATED.
SELECT
    CASE
        WHEN a.decision = 'approve' AND i.has_problem = 0 THEN '1 approved automatically (correct)'
        WHEN a.decision = 'approve' AND i.has_problem = 1 THEN '2 approved automatically (WRONG: problem missed)'
        WHEN a.decision = 'flag'    AND i.has_problem = 1 THEN '3 held for a person (correct: problem found)'
        ELSE                                                   '4 held for a person (false alarm)'
    END                                                         AS outcome,
    COUNT(*)                                                    AS invoices,
    ROUND(SUM(i.gross_total), 2)                                AS gross_value_eur,
    ROUND(SUM(i.money_at_risk_net), 2)                          AS money_at_risk_net_eur
FROM invoices AS i
JOIN agent_results AS a USING (invoice_id)
GROUP BY outcome
ORDER BY outcome;
