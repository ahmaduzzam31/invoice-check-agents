-- Money at risk by problem type: what was planted, and what the agent system caught.
-- Each problem invoice is counted once, under its main problem (hidden instructions are counted
-- under the overcharge they came with). All data is SIMULATED.
SELECT
    p.main_problem                                              AS problem_type,
    COUNT(*)                                                    AS invoices,
    ROUND(SUM(i.money_at_risk_net), 2)                          AS money_at_risk_net_eur,
    SUM(CASE WHEN a.decision = 'flag' THEN 1 ELSE 0 END)        AS invoices_flagged_by_agents,
    ROUND(SUM(CASE WHEN a.decision = 'flag' THEN i.money_at_risk_net ELSE 0 END), 2)
                                                                AS money_caught_net_eur
FROM invoices AS i
JOIN main_problem AS p USING (invoice_id)
JOIN agent_results AS a USING (invoice_id)
GROUP BY p.main_problem
ORDER BY money_at_risk_net_eur DESC;
