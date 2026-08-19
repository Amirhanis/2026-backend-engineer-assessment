with open('report_query.sql') as f:
    sql = f.read()

with open('one_day.sql', 'w', encoding='utf-8') as f:
    f.write(sql.replace('2026-06-30', '2026-05-01'))

with open('five_days.sql', 'w', encoding='utf-8') as f:
    f.write(sql.replace('2026-06-30', '2026-05-05'))

with open('one_tenant_full.sql', 'w', encoding='utf-8') as f:
    f.write(sql.replace('JOIN tenant t ON t.tenant_id = ol.tenant_id', "JOIN tenant t ON t.tenant_id = ol.tenant_id\n  AND t.tenant_id = 'T001'"))
