<?php
/**
 * edu-lite CMS —— Aegis 自建靶场（迷你校园站）。
 * 故意植入的漏洞类别（D2 三类）：upload_bypass / sqli / rce_deser。
 * 数据层：SQLite3 原生 API（静态 PHP 未带 pdo_sqlite）。
 * 仅限本地 scope.yaml 白名单实例使用（见项目 README 负责任披露）。
 */
require_once __DIR__ . '/gadget.php';

$INSTANCE = getenv('EDU_INSTANCE') ?: 'a';
$CFG = require __DIR__ . '/../config/instances.php';
$CFG = $CFG[$INSTANCE] ?? die('bad instance');
$DATA_DIR = __DIR__ . '/../data';

function db(): SQLite3
{
    global $INSTANCE, $DATA_DIR;
    static $db = null;
    if ($db === null) {
        $db = new SQLite3($DATA_DIR . '/' . $INSTANCE . '.db');
        $db->enableExceptions(true);
        $db->busyTimeout(5000);  // 多 worker 并发下的锁等待
        seed($db);
    }
    return $db;
}

function seed(SQLite3 $db): void
{
    global $CFG;
    $sig = $CFG['sig_field'];
    $db->exec("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, {$sig} TEXT DEFAULT '', avatar TEXT DEFAULT '', is_admin INTEGER DEFAULT 0)");
    $db->exec('CREATE TABLE IF NOT EXISTS news(id INTEGER PRIMARY KEY, title TEXT, body TEXT)');
    $db->exec('CREATE TABLE IF NOT EXISTS sessions(token TEXT, uid INTEGER)');
    $db->exec("INSERT OR IGNORE INTO users(id,username,password,is_admin) VALUES (1,'admin','admin123',1),(2,'alice','alice123',0),(3,'bob','bob123',0)");
    $db->exec("INSERT OR IGNORE INTO news(id,title,body) VALUES
        (1,'校园公告：期中考试安排','期中考试将于第十周进行，请各班做好复习。'),
        (2,'图书馆开放时间调整','即日起图书馆延长开放至晚上十点。'),
        (3,'运动会报名开始','春季运动会报名通道已开启。')");
}

function q_all(string $sql, array $params = []): array
{
    $st = db()->prepare($sql);
    foreach ($params as $i => $v) $st->bindValue($i + 1, $v);
    $res = $st->execute();
    $rows = [];
    while ($r = $res->fetchArray(SQLITE3_ASSOC)) $rows[] = $r;
    return $rows;
}

function html(string $title, string $body): string
{
    return "<!doctype html><html><head><meta charset='utf-8'><title>{$title} - edu-lite</title></head><body><h1>edu-lite 校园站</h1>{$body}</body></html>";
}

function session_user(): ?array
{
    global $CFG;
    $tok = $_COOKIE[$CFG['cookie']] ?? '';
    if (!$tok) return null;
    $rows = q_all('SELECT id, username, is_admin FROM users WHERE id = (SELECT uid FROM sessions WHERE token = ?)', [$tok]);
    return $rows[0] ?? null;
}

function require_admin(): array
{
    $u = session_user();
    if (!$u || $u['username'] !== 'admin') { http_response_code(403); die(html('403', '<p>admin only</p>')); }
    return $u;
}

// ---------- 路由 ----------
$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);
$method = $_SERVER['REQUEST_METHOD'];

if ($path === '/login' && $method === 'POST') {
    // 登录（brute_no_lock 插件 D4 接入：无速率限制是故意设定）
    $u = $_POST['username'] ?? ''; $p = $_POST['password'] ?? '';
    $rows = q_all('SELECT id,username FROM users WHERE username = ? AND password = ?', [$u, $p]);
    if (!$rows) { http_response_code(401); die(html('登录失败', '<p>bad credentials</p>')); }
    $tok = bin2hex(random_bytes(16));
    q_all('INSERT INTO sessions(token,uid) VALUES (?,?)', [$tok, $rows[0]['id']]);
    setcookie($CFG['cookie'], $tok);
    header('Location: /news');
    exit;
}

if ($path === '/logout') {
    setcookie($CFG['cookie'], '', time() - 3600);
    header('Location: /login');
    exit;
}

if ($path === '/login') {
    die(html('登录', '<form method="post" action="/login"><input name="username"><input name="password" type="password"><button>登录</button></form>'));
}

if ($path === '/news' || $path === '/') {
    if (!session_user()) { header('Location: /login'); exit; }
    $rows = q_all('SELECT title FROM news');
    $out = '';
    foreach ($rows as $r) $out .= "<li>" . htmlspecialchars($r['title']) . "</li>";
    die(html('新闻', "<p>搜索：<form action='/news/search'><input name='{$CFG['search_param']}'><button>go</button></form></p><ul>{$out}</ul>"));
}

if ($path === '/news/search') {
    if (!session_user()) { header('Location: /login'); exit; }
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
    die(html('搜索', "<p>关键字: " . htmlspecialchars($q) . "</p><ul>{$out}</ul>"));
}

if ($path === '/profile' && $method === 'POST') {
    $u = session_user() ?: die(html('401', '<p>login required</p>'));
    $sig = trim($_POST[$CFG['sig_field']] ?? '');
    q_all("UPDATE users SET {$CFG['sig_field']} = ? WHERE id = ?", [$sig, $u['id']]);
    // 【VULN xss_stored 的回显点】原样回显存储值（故意；存储型 XSS 判定面）
    header('Content-Type: application/json');
    echo json_encode(['ok' => true, 'sig' => $sig], JSON_UNESCAPED_UNICODE);
    exit;
}

if ($path === '/profile/avatar-url' && $method === 'POST') {
    $u = session_user() ?: die(html('401', '<p>login required</p>'));
    $url = trim($_POST[$CFG['avatar_url_field']] ?? '');
    // 【VULN ssrf】任意 URL 直接拉取，无协议/主机校验（内网探测面）
    $data = @file_get_contents($url);
    if ($data === false || $data === null) {
        http_response_code(502);
        die(json_encode(['ok' => false, 'error' => 'fetch failed']));
    }
    header('Content-Type: application/json');
    echo json_encode(['ok' => true, 'bytes' => strlen($data), 'head' => substr($data, 0, 80)]);
    exit;
}

if ($path === '/download') {
    $u = session_user() ?: die(html('401', '<p>login required</p>'));
    $f = $_GET[$CFG['dl_param']] ?? '';
    // 【VULN lfi】路径拼接无归一化/白名单，../ 可穿越出 files 目录
    $target_file = __DIR__ . '/../files/' . $f;
    if (!is_file($target_file)) { http_response_code(404); die(html('404', '<p>no such file</p>')); }
    header('Content-Type: text/plain; charset=utf-8');
    readfile($target_file);
    exit;
}

if ($path === '/admin/user-edit' && $method === 'POST') {
    $u = session_user() ?: die(html('401', '<p>login required</p>'));
    $uid = (int)($_POST[$CFG['uid_param']] ?? 0);
    $sig = trim($_POST[$CFG['sig_field']] ?? '');
    // 【VULN idor】任何登录用户可改任意 uid 的资料——缺所有权检查
    q_all("UPDATE users SET {$CFG['sig_field']} = ? WHERE id = ?", [$sig, $uid]);
    $rows = q_all('SELECT username FROM users WHERE id = ?', [$uid]);
    header('Content-Type: application/json');
    echo json_encode(['ok' => true, 'updated' => $rows[0]['username'] ?? "?"]);
    exit;
}

if ($path === '/profile/avatar' && $method === 'POST') {
    $u = session_user() ?: die(html('401', '<p>login required</p>'));
    $f = $_FILES[$CFG['avatar_field']] ?? null;
    if (!$f || $f['error'] !== UPLOAD_ERR_OK) { http_response_code(400); die(html('上传失败', '<p>no file</p>')); }
    $ext = strtolower(pathinfo($f['name'], PATHINFO_EXTENSION));
    // 【VULN upload_bypass】黑名单不完整（故意，漏 .phtml/.pht/.php4/.phar），且保留原文件名
    $blacklist = ['php', 'php3', 'php5'];
    if (in_array($ext, $blacklist)) { http_response_code(400); die(html('上传失败', '<p>forbidden extension</p>')); }
    $dest = __DIR__ . '/../uploads/' . $f['name'];
    // 故意保留原始文件名：含 ../ 路径穿越面（lfi 插件语料）；主漏洞是黑名单不完整
    move_uploaded_file($f['tmp_name'], $dest);
    die(html('上传成功', '<p>saved /uploads/' . htmlspecialchars($f['name']) . '</p>'));
}

if ($path === '/profile') {
    $u = session_user() ?: die(html('401', '<p>login required</p>'));
    $rows = q_all("SELECT {$CFG['sig_field']} AS sig FROM users WHERE id = ?", [$u['id']]);
    $sig = $rows[0]['sig'] ?? '';
    // 【VULN xss_stored】签名原样渲染（故意，不转义——存储型 XSS 的展示面；与 /news 列表的转义对照）
    die(html('个人资料', "<p>用户: {$u['username']}</p><p>签名: {$sig}</p>
        <form method='post' action='/profile'><input name='{$CFG['sig_field']}'><button>保存签名</button></form>
        <form method='post' action='/profile/avatar' enctype='multipart/form-data'><input type='file' name='{$CFG['avatar_field']}'><button>上传头像</button></form>
        <form method='post' action='/profile/avatar-url'><input name='{$CFG['avatar_url_field']}' placeholder='远程头像URL'><button>拉取</button></form>"));
}

if ($path === '/admin/export') {
    require_admin();
    $rows = q_all('SELECT username FROM users');
    $data = ['exported' => date('c'), 'users' => array_column($rows, 'username')];
    header('Content-Type: application/json');
    // 【VULN rce_deser 的另一半】导出用 serialize（与导入的 unserialize 对偶）
    echo json_encode(['backup' => serialize($data)]);
    exit;
}

if ($path === '/admin/import' && $method === 'POST') {
    require_admin();
    $body = json_decode(file_get_contents('php://input'), true) ?: [];
    // 【VULN rce_deser】用户可控数据直接 unserialize（EduBackupLogger POP 链可达）
    $data = unserialize($body[$CFG['import_param']] ?? '');
    if (is_array($data)) { /* 合法备份：仅确认可解析 */ }
    header('Content-Type: application/json');
    echo json_encode(['ok' => true]);
    exit;
}

http_response_code(404);
die(html('404', '<p>not found</p>'));
