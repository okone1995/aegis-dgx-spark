# 补丁模板：sqli（参数化查询，SQLite3 原生 API —— TARGET_BLOCK 与 app.php 逐字对齐）
TARGET_BLOCK: |
    $q = $_GET[$CFG['search_param']] ?? '';
    // 【VULN sqli】字符串拼接进 SQL（故意，插件 sqli 的注入点）
    $sql = "SELECT id, title, body FROM news WHERE title LIKE '%{$q}%' OR body LIKE '%{$q}%'";
    try {
        $res = db()->query($sql);
        $out = '';
        // 故意不转义：UNION 注入可控列 = xss_stored 插件（D4）预留的二阶 XSS 面（与 /news 列表的转义形成对照）
        while ($r = $res->fetchArray(SQLITE3_ASSOC)) $out .= '<li><b>' . $r['title'] . '</b> — ' . $r['body'] . '</li>';
    } catch (Exception $e) {
        http_response_code(500);
        die(html('错误', '<p>SQL error: ' . htmlspecialchars($e->getMessage()) . '</p>'));
    }
REPLACE_WITH: |
    $q = $_GET[$CFG['search_param']] ?? '';
    // [patched sqli] 参数化查询：q_all() 助手（prepare/bindValue/execute），用户输入不再拼接进 SQL
    $rows = q_all("SELECT id, title, body FROM news WHERE title LIKE ? OR body LIKE ?", ["%{$q}%", "%{$q}%"]);
    $out = '';
    foreach ($rows as $r) $out .= '<li><b>' . $r['title'] . '</b> — ' . $r['body'] . '</li>';

POST_CONDITIONS:
  - 全文不得再出现 PDO（静态 PHP 无 pdo_sqlite，PDO 写法会 fatal）
  - 任何用户输入不再进入 SQL 字符串拼接
  - 正常搜索行为不变（功能测试 test_news_list_and_benign_search / test_benign_search_no_result_is_clean 必须保持绿）
