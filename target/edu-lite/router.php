<?php
/** php -S 路由器：静态文件（uploads）直出，其余进应用。 */
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
if ($path !== '/' && preg_match('#^/uploads/#', $path)) {
    $file = __DIR__ . $path;
    if (is_file($file)) return false;  // 交给内置服务器静态直出
}
require __DIR__ . '/src/app.php';
