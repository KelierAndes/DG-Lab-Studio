import 'dart:io';

import 'package:flutter/material.dart';
import 'package:window_manager/window_manager.dart';

import 'app/shell.dart';
import 'engine/engine_state.dart';
import 'engine/engine_transport.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await windowManager.ensureInitialized();

  // 默认起 Python 侧车；DGSTUDIO_ENGINE=mock 时改用演示数据跑纯外壳。
  final useMock = Platform.environment['DGSTUDIO_ENGINE'] == 'mock';
  final engine = StudioEngine(
    useMock
        ? MockEngineTransport()
        : LoopbackEngineTransport(
            scriptDir: Platform.environment['DGSTUDIO_HOME'],
          ),
  );

  // 截图脚本用：置顶能让脚本指针稳定落在窗口上。
  final pinTop = Platform.environment['DGSTUDIO_TOPMOST'] == '1';

  const options = WindowOptions(
    size: Size(1320, 860),
    minimumSize: Size(1000, 640),
    center: true,
    backgroundColor: Colors.transparent,
    skipTaskbar: false,
    titleBarStyle: TitleBarStyle.hidden,
    title: 'DGStudio',
  );

  await windowManager.waitUntilReadyToShow(options, () async {
    // Windows 上的逐像素透明还需要 win32_window.cpp 里的 DWM sheet-of-glass 边距。
    await windowManager.setBackgroundColor(Colors.transparent);
    await windowManager.setAsFrameless();
    await windowManager.setHasShadow(true);
    if (pinTop) await windowManager.setAlwaysOnTop(true);
    await windowManager.show();
    await windowManager.focus();
  });

  runApp(DGStudioApp(engine: engine));
}

class DGStudioApp extends StatelessWidget {
  const DGStudioApp({super.key, required this.engine});

  final StudioEngine engine;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'DGStudio',
      debugShowCheckedModeBanner: false,
      themeMode: ThemeMode.dark,
      darkTheme: ThemeData(brightness: Brightness.dark, useMaterial3: true),
      theme: ThemeData(brightness: Brightness.light, useMaterial3: true),
      home: Scaffold(
        backgroundColor: Colors.transparent,
        body: AppShell(engine: engine),
      ),
    );
  }
}
