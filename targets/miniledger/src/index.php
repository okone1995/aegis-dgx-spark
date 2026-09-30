<?php
// MiniLedger —— 第二个目标（T11/S1 验收靶）
// 刻意与 edu-lite 不同：路由不同(/shipments)、查询形态不同(5 列表 + WHERE 等值)、机密表不同(api_keys)。
// 唯一漏洞（故意）：carrier 参数字符串拼接进 SQL。
declare(strict_types=1);

$DB = __DIR__ . '/../var/miniledger.sqlite';

function db(): SQLite3 {
    global $DB;
    static $conn = null;
    if ($conn === null) {
        $conn = new SQLite3($DB);
        $conn->busyTimeout(3000);
    }
    return $conn;
}

function q_all(string $sql, array $args = []): array {
    $st = db()->prepare($sql);
    foreach ($args as $i => $v) $st->bindValue($i + 1, $v, SQLITE3_TEXT);
    $res = $st->execute();
    $rows = [];
    while ($r = $res->fetchArray(SQLITE3_ASSOC)) $rows[] = $r;
    return $rows;
}

function esc(string $s): string {
    return htmlspecialchars($s, ENT_QUOTES, 'UTF-8');
}

function page(string $title, string $body): void {
    echo "<!doctype html><html><head><meta charset='utf-8'><title>" . esc($title) . "</title></head><body>";
    echo "<h1>MiniLedger · " . esc($title) . "</h1>";
    echo $body;
    echo "</body></html>";
}

$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';

if ($path === '/') {
    page('首页', "<p>货运台账服务。可用：<code>GET /shipments?carrier=SHP-101</code>、"
        . "<code>GET /health</code></p>");
    exit;
}

if ($path === '/health') {
    header('Content-Type: application/json');
    echo json_encode(['ok' => true, 'service' => 'miniledger']);
    exit;
}

if ($path === '/shipments') {
    $c = $_GET['carrier'] ?? '';
    if ($c === '') {
        // 空参数：给全部在运单（正常业务）
        $rows = q_all("SELECT id, ref, carrier, status, eta FROM shipments WHERE status <> 'CLOSED' ORDER BY id LIMIT 20");
        $out = '';
        foreach ($rows as $r) {
            $out .= '<tr><td>' . esc((string)$r['id']) . '</td><td>' . esc($r['ref']) . '</td><td>'
                . esc($r['carrier']) . '</td><td>' . esc($r['status']) . '</td><td>' . esc((string)$r['eta']) . '</td></tr>';
        }
        page('在运单', "<table border='1'><tr><th>ID</th><th>运单号</th><th>承运商</th><th>状态</th><th>预计到达</th></tr>"
            . $out . "</table>");
        exit;
    }
    // 【VULN sqli】carrier 字符串拼接进 SQL（故意；插件 sqli 的注入点）
    $sql = "SELECT id, ref, carrier, status, eta FROM shipments WHERE carrier = '{$c}'";
    try {
        $res = db()->query($sql);
        $out = '';
        while ($r = $res->fetchArray(SQLITE3_ASSOC)) {
            $out .= '<tr><td>' . esc((string)$r['id']) . '</td><td>' . esc($r['ref']) . '</td><td>'
                . esc($r['carrier']) . '</td><td>' . esc($r['status']) . '</td><td>' . esc((string)$r['eta']) . '</td></tr>';
        }
        page('按承运商查询', "<p>承运商：<b>" . esc($c) . "</b></p>"
            . "<table border='1'><tr><th>ID</th><th>运单号</th><th>承运商</th><th>状态</th><th>预计到达</th></tr>"
            . $out . "</table>");
    } catch (Throwable $e) {
        http_response_code(500);
        page('查询失败', "<p>ledger_unavailable</p>");
    }
    exit;
}

// 404
http_response_code(404);
page('未找到', "<p>route_unknown</p>");
