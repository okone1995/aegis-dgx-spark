<?php
// gadget 反序列化隔离测试（D2 验收诊断用）
require __DIR__ . '/../src/gadget.php';
$g = 'O:15:"EduBackupLogger":2:{s:7:"logFile";s:18:"uploads/pwn_t.txt";s:3:"msg";s:17:"VULN_CONFIRMED_TX";}';
echo "len=", strlen($g), "\n";
$o = unserialize($g);
var_dump($o);
