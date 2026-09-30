<?php
// MiniLedger 种子数据（幂等）。用法: php seed.php <db-path>
// 注意：SQLite 里双引号是标识符，字符串字面量必须用单引号 —— 这里全部用 PHP 单引号字符串包裹 SQL。
declare(strict_types=1);

$dbPath = $argv[1] ?? '';
if ($dbPath === '') {
    fwrite(STDERR, "usage: php seed.php <db-path>\n");
    exit(2);
}

$db = new SQLite3($dbPath);
$db->busyTimeout(3000);
$db->exec('CREATE TABLE IF NOT EXISTS shipments (id INTEGER PRIMARY KEY, ref TEXT, carrier TEXT, status TEXT, eta TEXT)');
$db->exec('CREATE TABLE IF NOT EXISTS api_keys (id INTEGER PRIMARY KEY, carrier_code TEXT, api_key TEXT, issued_on TEXT)');

if ((int)$db->querySingle('SELECT COUNT(*) FROM shipments') === 0) {
    $db->exec("INSERT INTO shipments (ref,carrier,status,eta) VALUES"
        . " ('ML-3001','SHP-101','IN_TRANSIT','2026-10-02'),"
        . " ('ML-3002','SHP-101','DELIVERED','2026-09-28'),"
        . " ('ML-3003','SHP-204','IN_TRANSIT','2026-10-05'),"
        . " ('ML-3004','SHP-204','CUSTOMS','2026-10-09'),"
        . " ('ML-3005','SHP-377','IN_TRANSIT','2026-10-11')");
}

if ((int)$db->querySingle('SELECT COUNT(*) FROM api_keys') === 0) {
    $db->exec("INSERT INTO api_keys (carrier_code,api_key,issued_on) VALUES"
        . " ('SHP-101','mlk_7f31c0a9e2b45d1c00000000','2026-05-02'),"
        . " ('SHP-204','mlk_2b8de15a7c903f6611111111','2026-06-17'),"
        . " ('SHP-377','mlk_9a04c7be13f8d2a522222222','2026-07-23')");
}

printf("seeded shipments=%d api_keys=%d\n",
    (int)$db->querySingle('SELECT COUNT(*) FROM shipments'),
    (int)$db->querySingle('SELECT COUNT(*) FROM api_keys'));
