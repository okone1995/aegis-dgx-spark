# 补丁模板：lfi（realpath 归一化 + 目录包含校验）
TARGET_BLOCK: |
    $f = $_GET[$CFG['dl_param']] ?? '';
    // 【VULN lfi】路径拼接无归一化/白名单，../ 可穿越出 files 目录
    $target_file = __DIR__ . '/../files/' . $f;
    if (!is_file($target_file)) { http_response_code(404); die(html('404', '<p>no such file</p>')); }
REPLACE_WITH: |
    $f = $_GET[$CFG['dl_param']] ?? '';
    // [patched lfi] realpath 归一化后必须仍位于 files 目录内
    $target_file = __DIR__ . '/../files/' . $f;
    $base = realpath(__DIR__ . '/../files');
    $real = realpath($target_file);
    if ($real === false || $base === false || strpos($real, $base) !== 0) {
        http_response_code(404); die(html('404', '<p>no such file</p>'));
    }

POST_CONDITIONS:
  - 合法下载（notice.txt）不受影响（test_download_notice_file 保持绿）
  - 任何含 ../（含编码变体）的请求必须 404
