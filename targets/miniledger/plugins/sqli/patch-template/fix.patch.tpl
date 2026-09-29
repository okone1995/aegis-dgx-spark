# 补丁模板：sqli（等值查询参数化）—— MiniLedger
# TARGET_BLOCK 必须与 targets/miniledger/src/index.php 的漏洞块**逐字对齐**。
TARGET_BLOCK: |
    $sql = "SELECT id, ref, carrier, status, eta FROM shipments WHERE carrier = '{$c}'";
    try {
        $res = db()->query($sql);
REPLACE_WITH: |
    $sql = "SELECT id, ref, carrier, status, eta FROM shipments WHERE carrier = :c";
    try {
        $st = db()->prepare($sql);
        $st->bindValue(':c', $c, SQLITE3_TEXT);
        $res = $st->execute();

POST_CONDITIONS:
  - 全文不得再出现 PDO（静态 PHP 无 pdo_sqlite，PDO 写法会 fatal）
  - 承运商参数不再进入 SQL 字符串拼接
  - 正常查询行为不变（功能测试 test_shipments_by_carrier_normal /
    test_unknown_carrier_returns_empty_table_not_error 必须保持绿）
