<?php
$config['des_key'] = trim(file_get_contents('/tmp/kt66-roundcube-key'));
$config['product_name'] = 'KT66 Mail';
$config['language'] = 'ko_KR';
$config['imap_conn_options'] = ['ssl' => ['verify_peer' => true, 'verify_peer_name' => true, 'peer_name' => 'mail.'.getenv('LAB_DOMAIN'), 'cafile' => '/mail-tls/server.crt']];
$config['smtp_conn_options'] = $config['imap_conn_options'];
$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';
$config['mail_domain'] = getenv('LAB_DOMAIN');
$config['session_lifetime'] = 30;
$config['display_next'] = true;
