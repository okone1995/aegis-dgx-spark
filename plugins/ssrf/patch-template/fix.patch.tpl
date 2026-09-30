# 补丁模板：ssrf（本地素材服务 allowlist + 私网/回环阻断）
TARGET_BLOCK: |
    $url = trim($_POST[$CFG['avatar_url_field']] ?? '');
    // 【VULN ssrf】任意 URL 直接拉取，无协议/主机校验（内网探测面）
    $data = @file_get_contents($url);
REPLACE_WITH: |
    $url = trim($_POST[$CFG['avatar_url_field']] ?? '');
    // [patched ssrf] 仅允许本地素材服务(127.0.0.1:30010)与公网；其余私网/回环全拒
    $_host = parse_url($url, PHP_URL_HOST);
    $_port = parse_url($url, PHP_URL_PORT) ?: (parse_url($url, PHP_URL_SCHEME) === 'https' ? 443 : 80);
    $_asset_ok = ($_host === '127.0.0.1' && $_port === 30010);
    if (!$_asset_ok) {
        $_ip = filter_var($_host, FILTER_VALIDATE_IP) ? $_host : gethostbyname($_host);
        if (!filter_var($_ip, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE)) {
            http_response_code(400);
            die(json_encode(['ok' => false, 'error' => 'internal address blocked']));
        }
    }
    $data = @file_get_contents($url);

POST_CONDITIONS:
  - 合法拉取 = 本地素材服务 http://127.0.0.1:30010/avatar.png（test_avatar_url_legit_fetch，全本地确定性）
  - 任意其他内网/回环目标（含 {{TARGET}}:808x）必须 400
  # 【已知局限（诚实声明）】校验用 gethostbyname 解析后交 file_get_contents 重新解析，
  # 存在 DNS rebinding/TOCTOU 理论绕过面；生产实现应对解析 IP 直连并逐跳校验重定向——
  # 与 payload_policy 的 blocklist 局限声明同范式，答辩主动交代。
