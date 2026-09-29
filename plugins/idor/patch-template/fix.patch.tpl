# 补丁模板：idor（所有权检查——自改放行，管理员例外，低权越权阻断）
TARGET_BLOCK: |
    $uid = (int)($_POST[$CFG['uid_param']] ?? 0);
    $sig = trim($_POST[$CFG['sig_field']] ?? '');
    // 【VULN idor】任何登录用户可改任意 uid 的资料——缺所有权检查
REPLACE_WITH: |
    $uid = (int)($_POST[$CFG['uid_param']] ?? 0);
    $sig = trim($_POST[$CFG['sig_field']] ?? '');
    // [patched idor] 所有权检查：仅允许自改，管理员例外（管理功能零回归——D5 评审 P1-1）
    if ($uid !== (int)$u['id'] && !(int)($u['is_admin'] ?? 0)) {
        http_response_code(403);
        die(json_encode(['ok' => false, 'error' => 'forbidden']));
    }

POST_CONDITIONS:
  - 低权用户（alice）改他人必须 403（attack/verify 组用 {{ALICE_SESSION}}）
  - 管理员改任意用户保持 200（test_admin_can_manage_others 必须绿——管理能力零回归）
  - 自改资料不受影响（test_user_edit_own_profile 保持绿）

