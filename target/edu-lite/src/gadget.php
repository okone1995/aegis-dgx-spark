<?php
/**
 * edu-lite 备份日志器 —— rce_deser 插件的 POP 链 gadget。
 * 漏洞设定（故意）：__destruct 落盘，路径与内容均来自对象属性。
 */
class EduBackupLogger
{
    public $logFile;
    public $msg;

    public function __destruct()
    {
        @file_put_contents($this->logFile, $this->msg);
    }
}
