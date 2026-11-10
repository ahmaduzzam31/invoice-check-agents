-- Suppliers ranked by money at risk on their invoices. All data is SIMULATED.
SELECT
    s.supplier_id,
    s.name                                                      AS supplier,
    COUNT(*)                                                    AS invoices,
    SUM(CASE WHEN i.has_problem = 1 THEN 1 ELSE 0 END)          AS invoices_with_problems,
    ROUND(SUM(i.gross_total), 2)                                AS invoiced_gross_eur,
    ROUND(SUM(i.money_at_risk_net), 2)                          AS money_at_risk_net_eur,
    GROUP_CONCAT(CASE WHEN i.has_problem = 1 THEN i.scenario END, ', ') AS problem_types
FROM invoices AS i
JOIN erp.suppliers AS s USING (supplier_id)
GROUP BY s.supplier_id, s.name
ORDER BY money_at_risk_net_eur DESC, invoices_with_problems DESC, s.supplier_id;
