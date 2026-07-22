CREATE OR REPLACE PROCEDURE proc_daily_reconciliation (
    p_date IN DATE
) AS
    v_count NUMBER := 0;
BEGIN
    SELECT NVL(SUM(amount), 0) INTO v_count FROM payments
     WHERE TRUNC(processed_at) = p_date;
    log_run('SUCCESS', v_count);
END proc_daily_reconciliation;
/

CREATE OR REPLACE PROCEDURE log_run (
    p_status IN VARCHAR2,
    p_count  IN NUMBER
) AS
BEGIN
    INSERT INTO run_log (status, cnt) VALUES (p_status, DECODE(p_count, 0, 'EMPTY', 'OK'));
    RECONCILIATION_INTERFACES.export_csv('FAFIF', 'RUN_LOG');
END log_run;
/
