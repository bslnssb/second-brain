#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""env_msgs.py — 环境体检文案的西班牙语 / 葡萄牙语 / 印地语版本（第 176 项）

为什么单独一个文件：env_check.py 里 `_EN` 只有英文，`_use_en()` 把西/葡/印地
全指到英文，用户切成这三种语言后「体检」面板和「复制诊断」的正文仍然是英文，
看起来就像「只有英语和中文」。文案量大（69 条 × 3 种），塞进 env_check 会把
判定逻辑淹掉，所以按语言拆到这里；**键集合必须与 env_check._EN 完全一致**，
preflight 会比对（漏翻会直接红）。

占位符（%s / %d / %.1f）必须与英文版同序同类型 —— 这些串是用 % 格式化的，
少一个占位符就是运行期 TypeError，不是排版问题。
"""

# 西班牙语
ES = {
    'wv_missing_t': 'Falta el runtime de WebView2',
    'wv_missing_d': 'La ventana no puede abrirse: ves una pantalla en blanco o el programa '
                    'se cierra al instante, aunque el servicio ya esté en marcha',
    'wv_missing_f': 'Instálalo una vez (unos 2 MB): %s',
    'wv_old_t': 'El runtime de WebView2 es antiguo (%s)',
    'wv_old_d': 'Faltan APIs nuevas; algunas páginas pueden mostrarse mal',
    'wv_old_f': 'Actualiza a la última versión: %s',
    'wv_ok_t': 'Runtime de WebView2 %s',

    'data_bad_t': 'La carpeta del programa no admite escritura',
    'data_bad_d': 'Carpeta de datos %s %s',
    'data_moved': 'se cambió a %s',
    'data_nofb': 'y la carpeta alternativa tampoco admite escritura',
    'data_bad_f': 'Mueve el programa a una carpeta con permiso de escritura '
                  '(p. ej. D:/SecondBrain) y vuelve a iniciarlo',
    'data_ok_t': 'La carpeta de datos admite escritura: %s',
    'unc_t': 'La carpeta de datos está en una ruta de red (%s)',
    'unc_d': 'El bloqueo de archivos de SQLite sobre SMB no es fiable: el índice puede '
             'corromperse o dar "database is locked"',
    'unc_f': 'Pon el programa en un disco local (p. ej. D:/SecondBrain) y vuelve a iniciarlo',
    'sync_t': 'La carpeta de datos está dentro de una carpeta sincronizada (%s)',
    'sync_d': 'El índice pasa de 1 GB y se escribe sin parar; la sincronización lo vuelve '
              'a subir y bloquea los archivos un rato',
    'sync_f': 'Saca el programa de la carpeta sincronizada; para copias usa settings.json',

    'disk_low_t': 'No queda espacio libre suficiente (%d MB)',
    'disk_low_d': 'Indexar necesita escribir; si se llena a medias el índice queda incompleto',
    'disk_low_f': 'Libera al menos 1 GB antes de indexar',
    'disk_some_t': 'Queda poco espacio libre (%d MB)',
    'disk_ok_t': '%.1f GB libres',

    'screen_t': 'Pantalla principal %d×%d @%d%% (lógica %d×%d)',
    'screen_small_t': 'El área útil de pantalla es pequeña',
    'screen_small_d': 'Lógica %d×%d; la ventana rápida y la principal se reducen para caber',
    'screen_small_f': 'Nada se rompe, pero las ventanas quedarán apretadas',
    'multi_t': 'Se detectaron %d pantallas',

    'remote_t': 'Ejecutándose en una sesión de Escritorio remoto',
    'remote_d': 'WebView2 puede mostrar bloques negros o dejar de refrescar por RDP',
    'remote_f': 'Si falla, inicia con --disable-gpu o usa una pantalla local',
    'gpu_t': 'Los gráficos usan el adaptador básico (%s)',
    'gpu_d': 'Sin aceleración por hardware; WebView2 puede parpadear o mostrar bloques negros',
    'gpu_f': 'Instala el controlador de la GPU o inicia con --disable-gpu',

    'db_missing_t': 'Aún no hay índice (se crea al primer arranque)',
    'db_ok_t': 'El índice está sano (%.1f MB)',
    'db_busy_t': 'El índice se está escribiendo; se omite la comprobación de integridad',
    'db_busy_d': 'Es normal en el primer escaneo o al reconstruir: vuelve a comprobarlo más tarde',
    'db_bad_t': 'El índice está dañado (%s)',
    'db_bad_d': 'Al arrancar se archiva el archivo dañado y se crea un índice vacío; '
                'hay que indexar otra vez',
    'db_bad_f': 'Si se repite, revisa la salud del disco (smartctl / chkdsk)',

    'port_running_t': 'Ya hay otra instancia en marcha (puerto %d)',
    'port_free_t': 'Puerto %d disponible',
    'port_full_t': 'Los puertos 8765–8776 están todos ocupados',
    'port_full_d': 'El servicio no puede arrancar y la interfaz no puede conectarse',
    'port_full_f': 'Cierra lo que ocupe esos puertos (netstat -ano | findstr 8765)',
    'port_other_t': 'El puerto %d es de una instancia con otra carpeta de datos',
    'port_other_d': 'Lo dejó otra sesión de Windows u otra instalación; no se reutiliza '
                    '(esta toma el siguiente libre)',

    'sys_t': '%s · %s · %s · %.1f GB de RAM',
    'sys_admin': 'Administrador',
    'sys_user': 'usuario estándar',
    'font_ok_t': 'Microsoft YaHei está disponible',
    'font_bad_t': 'Falta Microsoft YaHei',
    'font_bad_d': 'El texto chino puede verse como cuadros',
    'font_bad_f': 'Instala la fuente "Microsoft YaHei" (viene con Windows; '
                  'tu imagen la tenía quitada)',
    'longpath_t': 'El soporte de rutas largas está desactivado',
    'longpath_d': 'Las rutas de más de 260 caracteres se omiten (el resto se indexa bien)',
    'longpath_f': 'Si faltan archivos en el índice, activa LongPathsEnabled y vuelve a escanear',
    'theme_t': 'El sistema está en modo oscuro',
    'contrast_t': 'El alto contraste está activado',
    'nonascii_t': 'La carpeta de datos contiene caracteres no ASCII',

    'rpt_title': 'Second Brain · revisión del entorno    %s',
    'rpt_plan': 'Ajuste: principal %d×%d · rápida %d de ancho (pantalla %d×%d)%s',
    'rpt_gpu': ' · se recomienda desactivar la aceleración por hardware',
    'rpt_concl': 'Resultado: %s',
    'rpt_fail': '%d no disponible — hay que arreglarlo',
    'rpt_warn': '%d a revisar, sigue siendo usable',
    'rpt_ok': 'el entorno se ve bien',
}

# 葡萄牙语（巴西）
PT = {
    'wv_missing_t': 'O runtime do WebView2 não está instalado',
    'wv_missing_d': 'A janela não abre: você vê uma tela em branco ou o programa fecha na '
                    'hora, mesmo com o serviço já rodando',
    'wv_missing_f': 'Instale uma vez (~2 MB): %s',
    'wv_old_t': 'O runtime do WebView2 está antigo (%s)',
    'wv_old_d': 'Faltam APIs novas; algumas páginas podem renderizar errado',
    'wv_old_f': 'Atualize para a versão mais recente: %s',
    'wv_ok_t': 'Runtime do WebView2 %s',

    'data_bad_t': 'A pasta do programa não permite gravação',
    'data_bad_d': 'Pasta de dados %s %s',
    'data_moved': 'foi alterada para %s',
    'data_nofb': 'e a pasta alternativa também não permite gravação',
    'data_bad_f': 'Mova o programa para uma pasta com permissão de gravação '
                  '(ex.: D:/SecondBrain) e abra de novo',
    'data_ok_t': 'Pasta de dados gravável: %s',
    'unc_t': 'A pasta de dados está em um caminho de rede (%s)',
    'unc_d': 'O bloqueio de arquivos do SQLite via SMB não é confiável: o índice pode '
             'corromper ou dar "database is locked"',
    'unc_f': 'Coloque o programa em um disco local (ex.: D:/SecondBrain) e abra de novo',
    'sync_t': 'A pasta de dados está dentro de uma pasta sincronizada (%s)',
    'sync_d': 'O índice passa de 1 GB e é gravado o tempo todo; o sincronizador reenvia '
              'e trava os arquivos por alguns segundos',
    'sync_f': 'Tire o programa da pasta sincronizada; para backup use o settings.json',

    'disk_low_t': 'Espaço livre insuficiente (%d MB)',
    'disk_low_d': 'Indexar precisa gravar; se acabar no meio, o índice fica incompleto',
    'disk_low_f': 'Libere pelo menos 1 GB antes de indexar',
    'disk_some_t': 'Pouco espaço livre (%d MB)',
    'disk_ok_t': '%.1f GB livres',

    'screen_t': 'Tela principal %d×%d @%d%% (lógica %d×%d)',
    'screen_small_t': 'A área útil da tela é pequena',
    'screen_small_d': 'Lógica %d×%d; a janela rápida e a principal encolhem para caber',
    'screen_small_f': 'Nada quebra, mas as janelas ficam apertadas',
    'multi_t': '%d telas detectadas',

    'remote_t': 'Rodando em uma sessão de Área de Trabalho Remota',
    'remote_d': 'O WebView2 pode mostrar blocos pretos ou parar de atualizar via RDP',
    'remote_f': 'Se falhar, inicie com --disable-gpu ou use uma tela local',
    'gpu_t': 'Os gráficos usam o adaptador básico (%s)',
    'gpu_d': 'Sem aceleração de hardware; o WebView2 pode piscar ou mostrar blocos pretos',
    'gpu_f': 'Instale o driver da GPU ou inicie com --disable-gpu',

    'db_missing_t': 'Ainda não há índice (criado no primeiro início)',
    'db_ok_t': 'O índice está íntegro (%.1f MB)',
    'db_busy_t': 'O índice está sendo gravado; verificação de integridade ignorada',
    'db_busy_d': 'Normal na primeira varredura ou ao reconstruir — verifique de novo mais tarde',
    'db_bad_t': 'O índice está corrompido (%s)',
    'db_bad_d': 'Ao iniciar, o arquivo ruim é arquivado e um índice vazio é criado; '
                'é preciso indexar de novo',
    'db_bad_f': 'Se repetir, verifique a saúde do disco (smartctl / chkdsk)',

    'port_running_t': 'Já existe outra instância rodando (porta %d)',
    'port_free_t': 'Porta %d disponível',
    'port_full_t': 'As portas 8765–8776 estão todas em uso',
    'port_full_d': 'O serviço não inicia e a interface não consegue conectar',
    'port_full_f': 'Feche o que ocupa essas portas (netstat -ano | findstr 8765)',
    'port_other_t': 'A porta %d é de uma instância com outra pasta de dados',
    'port_other_d': 'Deixada por outra sessão do Windows ou outra instalação; não é '
                    'reutilizada (esta pega a próxima livre)',

    'sys_t': '%s · %s · %s · %.1f GB de RAM',
    'sys_admin': 'Administrador',
    'sys_user': 'usuário padrão',
    'font_ok_t': 'Microsoft YaHei disponível',
    'font_bad_t': 'Microsoft YaHei ausente',
    'font_bad_d': 'O texto em chinês pode aparecer como quadrados',
    'font_bad_f': 'Instale a fonte "Microsoft YaHei" (vem com o Windows; '
                  'sua imagem veio sem ela)',
    'longpath_t': 'O suporte a caminhos longos está desligado',
    'longpath_d': 'Caminhos com mais de 260 caracteres são ignorados (o resto indexa normal)',
    'longpath_f': 'Se faltarem arquivos no índice, ative LongPathsEnabled e varra de novo',
    'theme_t': 'O sistema está no modo escuro',
    'contrast_t': 'Alto contraste ativado',
    'nonascii_t': 'A pasta de dados tem caracteres não ASCII',

    'rpt_title': 'Second Brain · verificação do ambiente    %s',
    'rpt_plan': 'Ajuste: principal %d×%d · rápida %d de largura (tela %d×%d)%s',
    'rpt_gpu': ' · recomenda-se desligar a aceleração de hardware',
    'rpt_concl': 'Resultado: %s',
    'rpt_fail': '%d indisponível — precisa corrigir',
    'rpt_warn': '%d para olhar, ainda dá para usar',
    'rpt_ok': 'o ambiente está bom',
}

# 印地语
HI = {
    'wv_missing_t': 'WebView2 रनटाइम नहीं मिला',
    'wv_missing_d': 'विंडो नहीं खुलती: सफ़ेद स्क्रीन दिखती है या प्रोग्राम तुरंत बंद हो जाता '
                    'है, जबकि सेवा पहले से चल रही होती है',
    'wv_missing_f': 'एक बार इंस्टॉल करें (लगभग 2MB): %s',
    'wv_old_t': 'WebView2 रनटाइम पुराना है (%s)',
    'wv_old_d': 'कुछ नई API नहीं हैं; कुछ पेज गलत दिख सकते हैं',
    'wv_old_f': 'नवीनतम संस्करण पर अपडेट करें: %s',
    'wv_ok_t': 'WebView2 रनटाइम %s',

    'data_bad_t': 'प्रोग्राम फ़ोल्डर में लिखा नहीं जा सकता',
    'data_bad_d': 'डेटा फ़ोल्डर %s %s',
    'data_moved': 'बदलकर %s कर दिया गया',
    'data_nofb': 'और वैकल्पिक फ़ोल्डर में भी नहीं लिखा जा सकता',
    'data_bad_f': 'प्रोग्राम को ऐसे फ़ोल्डर में रखें जहाँ लिखने की अनुमति हो '
                  '(जैसे D:/SecondBrain) और फिर शुरू करें',
    'data_ok_t': 'डेटा फ़ोल्डर में लिखा जा सकता है: %s',
    'unc_t': 'डेटा फ़ोल्डर नेटवर्क पाथ पर है (%s)',
    'unc_d': 'SMB पर SQLite की फ़ाइल लॉकिंग भरोसेमंद नहीं: इंडेक्स खराब हो सकता है या '
             '"database is locked" आ सकता है',
    'unc_f': 'प्रोग्राम को लोकल डिस्क पर रखें (जैसे D:/SecondBrain) और फिर शुरू करें',
    'sync_t': 'डेटा फ़ोल्डर सिंक फ़ोल्डर के अंदर है (%s)',
    'sync_d': 'इंडेक्स 1 GB से बड़ा है और लगातार लिखा जाता है; सिंक टूल उसे दोबारा '
              'अपलोड करता है और फ़ाइलें थोड़ी देर लॉक कर देता है',
    'sync_f': 'प्रोग्राम को सिंक फ़ोल्डर से बाहर ले जाएँ; बैकअप के लिए settings.json अलग रखें',

    'disk_low_t': 'खाली डिस्क स्थान काफ़ी नहीं है (%d MB)',
    'disk_low_d': 'इंडेक्स बनाने में लिखना पड़ता है; बीच में जगह खत्म हो तो इंडेक्स अधूरा रह जाता है',
    'disk_low_f': 'इंडेक्स बनाने से पहले कम से कम 1 GB खाली करें',
    'disk_some_t': 'खाली डिस्क स्थान कम है (%d MB)',
    'disk_ok_t': '%.1f GB खाली',

    'screen_t': 'मुख्य स्क्रीन %d×%d @%d%% (तार्किक %d×%d)',
    'screen_small_t': 'स्क्रीन का उपलब्ध क्षेत्र छोटा है',
    'screen_small_d': 'तार्किक %d×%d; क्विक विंडो और मुख्य विंडो सिकुड़कर फ़िट होती हैं',
    'screen_small_f': 'कुछ टूटता नहीं, पर विंडो तंग लगेंगी',
    'multi_t': '%d स्क्रीन मिलीं',

    'remote_t': 'रिमोट डेस्कटॉप सत्र में चल रहा है',
    'remote_d': 'RDP पर WebView2 काले धब्बे दिखा सकता है या रीफ़्रेश होना बंद कर सकता है',
    'remote_f': 'दिक्कत हो तो --disable-gpu के साथ शुरू करें या लोकल स्क्रीन इस्तेमाल करें',
    'gpu_t': 'ग्राफ़िक्स बेसिक डिस्प्ले एडाप्टर पर है (%s)',
    'gpu_d': 'हार्डवेयर एक्सेलरेशन नहीं; WebView2 झिलमिला सकता है या काले धब्बे दिखा सकता है',
    'gpu_f': 'GPU ड्राइवर इंस्टॉल करें, या --disable-gpu के साथ शुरू करें',

    'db_missing_t': 'अभी कोई इंडेक्स नहीं (पहली बार चलने पर बनता है)',
    'db_ok_t': 'इंडेक्स ठीक है (%.1f MB)',
    'db_busy_t': 'इंडेक्स में लिखा जा रहा है; इंटीग्रिटी जाँच छोड़ी गई',
    'db_busy_d': 'पहली स्कैन या रीबिल्ड के दौरान सामान्य है — बाद में फिर जाँचें',
    'db_bad_t': 'इंडेक्स खराब है (%s)',
    'db_bad_d': 'शुरू होते समय खराब फ़ाइल संग्रह कर दी जाती है और खाली इंडेक्स बनता है; '
                'एक बार फिर इंडेक्स करना होगा',
    'db_bad_f': 'बार-बार हो तो डिस्क की सेहत जाँचें (smartctl / chkdsk)',

    'port_running_t': 'एक और इंस्टेंस चल रहा है (पोर्ट %d)',
    'port_free_t': 'पोर्ट %d उपलब्ध है',
    'port_full_t': 'पोर्ट 8765–8776 सभी व्यस्त हैं',
    'port_full_d': 'सेवा शुरू नहीं हो सकती और UI कनेक्ट नहीं हो सकता',
    'port_full_f': 'जो इन पोर्ट पर है उसे बंद करें (netstat -ano | findstr 8765)',
    'port_other_t': 'पोर्ट %d किसी और डेटा फ़ोल्डर वाले इंस्टेंस का है',
    'port_other_d': 'किसी दूसरे Windows सत्र या दूसरी इंस्टॉल ने छोड़ा है; दोबारा नहीं '
                    'लिया जाता (यह अगला खाली पोर्ट लेता है)',

    'sys_t': '%s · %s · %s · %.1f GB RAM',
    'sys_admin': 'व्यवस्थापक',
    'sys_user': 'सामान्य उपयोगकर्ता',
    'font_ok_t': 'Microsoft YaHei उपलब्ध है',
    'font_bad_t': 'Microsoft YaHei नहीं है',
    'font_bad_d': 'चीनी पाठ बक्सों जैसा दिख सकता है',
    'font_bad_f': '"Microsoft YaHei" फ़ॉन्ट इंस्टॉल करें (Windows के साथ आता है; '
                  'आपकी इमेज से हटा दिया गया था)',
    'longpath_t': 'लंबे पाथ का समर्थन बंद है',
    'longpath_d': '260 अक्षरों से लंबे पाथ छोड़ दिए जाते हैं (बाकी ठीक इंडेक्स होते हैं)',
    'longpath_f': 'अगर फ़ाइलें इंडेक्स में नहीं मिलें, तो LongPathsEnabled चालू करके फिर स्कैन करें',
    'theme_t': 'सिस्टम डार्क मोड में है',
    'contrast_t': 'हाई कॉन्ट्रास्ट चालू है',
    'nonascii_t': 'डेटा फ़ोल्डर में ग़ैर-ASCII अक्षर हैं',

    'rpt_title': 'Second Brain · पर्यावरण जाँच    %s',
    'rpt_plan': 'फ़िट: मुख्य %d×%d · क्विक %d चौड़ी (स्क्रीन %d×%d)%s',
    'rpt_gpu': ' · हार्डवेयर एक्सेलरेशन बंद रखने की सलाह',
    'rpt_concl': 'नतीजा: %s',
    'rpt_fail': '%d उपलब्ध नहीं — पहले ठीक करना ज़रूरी',
    'rpt_warn': '%d पर ध्यान दें, काम चल जाएगा',
    'rpt_ok': 'पर्यावरण ठीक लग रहा है',
}

TABLES = {'es': ES, 'pt': PT, 'hi': HI}
