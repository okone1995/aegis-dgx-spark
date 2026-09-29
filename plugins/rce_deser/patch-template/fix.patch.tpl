# 补丁模板：rce_deser（弃用 serialize 往返 —— import/export 对偶两处，与 app.php 逐字对齐）
TARGET_BLOCK: |
    $body = json_decode(file_get_contents('php://input'), true) ?: [];
    // 【VULN rce_deser】用户可控数据直接 unserialize（EduBackupLogger POP 链可达）
    $data = unserialize($body[$CFG['import_param']] ?? '');
    if (is_array($data)) { /* 合法备份：仅确认可解析 */ }
REPLACE_WITH: |
    $body = json_decode(file_get_contents('php://input'), true) ?: [];
    // [patched rce_deser] 弃用 serialize 往返：仅接受 JSON 数组，杜绝对象实例化（gadget 不可达）
    $raw = $body[$CFG['import_param']] ?? '';
    $data = is_string($raw) ? json_decode($raw, true) : null;
    if (!is_array($data)) $data = null;
TARGET_BLOCK_2: |
    // 【VULN rce_deser 的另一半】导出用 serialize（与导入的 unserialize 对偶）
    echo json_encode(['backup' => serialize($data)]);
REPLACE_WITH_2: |
    // [patched rce_deser] 导出改 JSON 往返（与导入对偶，unserialize 全仓消失）
    echo json_encode(['backup' => json_encode($data, JSON_UNESCAPED_UNICODE)]);

POST_CONDITIONS:
  - 全文不再出现 unserialize( 与 serialize(（gadget 触发路径消失）
  - 功能测试 test_admin_export_import_roundtrip 保持绿（导出格式随补丁同步，往返自洽）
