# 补丁模板：upload_bypass（白名单 + 随机重命名 —— 与 app.php 逐字对齐）
TARGET_BLOCK: |
    $ext = strtolower(pathinfo($f['name'], PATHINFO_EXTENSION));
    // 【VULN upload_bypass】黑名单不完整（故意，漏 .phtml/.pht/.php4/.phar），且保留原文件名
    $blacklist = ['php', 'php3', 'php5'];
    if (in_array($ext, $blacklist)) { http_response_code(400); die(html('上传失败', '<p>forbidden extension</p>')); }
    $dest = __DIR__ . '/../uploads/' . $f['name'];
    // 故意保留原始文件名：含 ../ 路径穿越面（lfi 插件语料）；主漏洞是黑名单不完整
    move_uploaded_file($f['tmp_name'], $dest);
    die(html('上传成功', '<p>saved /uploads/' . htmlspecialchars($f['name']) . '</p>'));
REPLACE_WITH: |
    $ext = strtolower(pathinfo($f['name'], PATHINFO_EXTENSION));
    // [patched upload_bypass] 黑名单 → 白名单 + 随机重命名（路径穿越面一并消除）
    $allowlist = ['jpg', 'jpeg', 'png', 'gif', 'webp'];
    if (!in_array($ext, $allowlist)) { http_response_code(400); die(html('上传失败', '<p>extension not allowed</p>')); }
    $safeName = bin2hex(random_bytes(8)) . '.' . $ext;
    $dest = __DIR__ . '/../uploads/' . $safeName;
    move_uploaded_file($f['tmp_name'], $dest);
    die(html('上传成功', '<p>saved /uploads/' . htmlspecialchars($safeName) . '</p>'));

POST_CONDITIONS:
  - 功能测试 test_avatar_upload_valid_png 保持绿（断言解析实际保存名，兼容随机重命名）
  - 存量恶意文件由重部署清空（deploy 重建 runtime），verify 重放必须全 404/400
