<?php
// A/B 双实例命名映射：仅参数/字段/cookie 名不同，逻辑完全一致（held-out 纪律，防训练即测试）
return [
    'a' => [
        'search_param' => 'q',
        'avatar_field' => 'avatar',
        'sig_field' => 'signature',
        'import_param' => 'backup',
        'cookie' => 'edu_session',
        'avatar_url_field' => 'avatar_url',
        'dl_param' => 'file',
        'uid_param' => 'uid',
    ],
    'b' => [
        'search_param' => 'keyword',
        'avatar_field' => 'picture',
        'sig_field' => 'motto',
        'import_param' => 'restore',
        'cookie' => 'session_key',
        'avatar_url_field' => 'pic_url',
        'dl_param' => 'path',
        'uid_param' => 'target',
    ],
];
