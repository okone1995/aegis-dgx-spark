# 补丁模板：brute_no_lock（失败计数锁定——参数化写库，两处改动）
TARGET_BLOCK: |
    $u = $_POST['username'] ?? ''; $p = $_POST['password'] ?? '';
    $rows = q_all('SELECT id,username FROM users WHERE username = ? AND password = ?', [$u, $p]);
    if (!$rows) { http_response_code(401); die(html('登录失败', '<p>bad credentials</p>')); }
REPLACE_WITH: |
    $u = $_POST['username'] ?? ''; $p = $_POST['password'] ?? '';
    // [patched brute_no_lock] 同用户名失败计数，5 次锁 10 分钟
    db()->exec('CREATE TABLE IF NOT EXISTS login_locks(username TEXT PRIMARY KEY, fails INTEGER DEFAULT 0, until INTEGER DEFAULT 0)');
    $lk = q_all('SELECT fails, until FROM login_locks WHERE username = ?', [$u]);
    if ($lk && (int)$lk[0]['until'] > time()) {
        http_response_code(429); die(html('已锁定', '<p>too many attempts, try later</p>'));
    }
    $rows = q_all('SELECT id,username FROM users WHERE username = ? AND password = ?', [$u, $p]);
    if (!$rows) {
        q_all("INSERT INTO login_locks(username,fails,until) VALUES (?,1,0) ON CONFLICT(username) DO UPDATE SET fails = fails + 1", [$u]);
        $fl = q_all('SELECT fails FROM login_locks WHERE username = ?', [$u]);
        if ((int)$fl[0]['fails'] >= 5) {
            q_all('UPDATE login_locks SET until = ? WHERE username = ?', [time() + 600, $u]);
        }
        http_response_code(401); die(html('登录失败', '<p>bad credentials</p>'));
    }
    q_all('UPDATE login_locks SET fails = 0, until = 0 WHERE username = ?', [$u]);

POST_CONDITIONS:
  - 正常登录不受影响（test_login_and_news_flow / test_bad_login_rejected 保持绿——单次失败不锁）
  - 第 6 次失败尝试必须 429
