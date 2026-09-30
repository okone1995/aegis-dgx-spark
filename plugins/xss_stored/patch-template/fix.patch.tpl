# 补丁模板：xss_stored（两处输出点转义——存储接口回显 + 个人资料页渲染）
TARGET_BLOCK: |
    // 【VULN xss_stored 的回显点】原样回显存储值（故意；存储型 XSS 判定面）
    header('Content-Type: application/json');
    echo json_encode(['ok' => true, 'sig' => $sig], JSON_UNESCAPED_UNICODE);
REPLACE_WITH: |
    // [patched xss_stored] 输出转义（存储接口回显点）
    header('Content-Type: application/json');
    echo json_encode(['ok' => true, 'sig' => htmlspecialchars($sig, ENT_QUOTES)], JSON_UNESCAPED_UNICODE);
TARGET_BLOCK_2: |
    // 【VULN xss_stored】签名原样渲染（故意，不转义——存储型 XSS 的展示面；与 /news 列表的转义对照）
    die(html('个人资料', "<p>用户: {$u['username']}</p><p>签名: {$sig}</p>
REPLACE_WITH_2: |
    // [patched xss_stored] 渲染转义（展示面）
    die(html('个人资料', "<p>用户: {$u['username']}</p><p>签名: " . htmlspecialchars($sig, ENT_QUOTES) . "</p>

POST_CONDITIONS:
  - 纯文本签名往返不受影响（test_signature_roundtrip 保持绿——纯 ASCII marker 转义后不变）
  - 任何含 HTML/事件属性的签名必须被转义为实体
