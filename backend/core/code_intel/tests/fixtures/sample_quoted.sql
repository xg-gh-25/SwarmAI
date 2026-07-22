set define off;
 CREATE OR REPLACE PROCEDURE "I2B2_LOAD_DATA"
(
  trial_id   IN VARCHAR2
 ,top_node   in varchar2
)
AS
  sqlText varchar2(1000);
BEGIN
  sqlText := 'alter table x drop partition "' || trial_id || '"';
  execute immediate(sqlText);
END;
/
