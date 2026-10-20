import 'package:flutter/material.dart' hide Stack;
import 'package:flutter_test/flutter_test.dart';

import 'package:dgstudio/app/shell.dart';
import 'package:dgstudio/engine/engine_state.dart';
import 'package:dgstudio/engine/engine_transport.dart';
import 'package:dgstudio/pages/app_pages.dart';

import 'harness.dart';

void main() {
  Future<(AppShellState, MockEngineTransport)> bootConnect(WidgetTester tester) async {
    final transport = MockEngineTransport(heartbeat: false);
    final engine = StudioEngine(transport);
    addTearDown(engine.dispose);
    final shell = await bootShell<AppShellState>(tester, AppShell(engine: engine),
        size: const Size(1320, 1400));
    await tester.pump(const Duration(milliseconds: 140));
    shell.nav.go(AppPage.connect);
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    return (shell, transport);
  }

  Finder inCard(String key) => find.byKey(ValueKey('card-$key'));

  // 旧界面是三条通道平铺，本地中继只是 v4/v3 自己的选项，不是第四种连接。
  testWidgets('三条通道平铺，各自报自己的状态', (tester) async {
    await bootConnect(tester);
    for (final key in ['v4', 'v3', 'ble']) {
      expect(inCard(key), findsOneWidget, reason: '$key 通道卡要在场');
    }
    expect(find.text('Socket V4 连接'), findsOneWidget);
    expect(find.text('Socket V3 连接'), findsOneWidget);
    expect(find.textContaining('蓝牙直连', findRichText: true), findsWidgets);
    expect(find.text('本地中继'), findsNothing, reason: '本地中继不该再是一种通道');
    expect(find.descendant(of: inCard('v3'), matching: find.text('未启用')), findsOneWidget,
        reason: '没连的 v3 通道要标未启用');
    expect(find.descendant(of: inCard('ble'), matching: find.text('已连接 1 台')), findsOneWidget);
  });

  testWidgets('设备按通道分列，只断一条不影响另一条', (tester) async {
    final (_, transport) = await bootConnect(tester);
    // 负鼠在蓝牙通道，郊狼与灵猫在 v4 通道。
    expect(find.descendant(of: inCard('v4'), matching: find.text('项圈 · 主')), findsOneWidget);
    expect(find.byKey(const ValueKey('channel-device-slot_3')), findsOneWidget);
    expect(find.descendant(of: inCard('v4'), matching: find.byKey(const ValueKey('channel-device-slot_3'))),
        findsNothing);

    await tester.tap(find.byKey(const ValueKey('disconnect-v4')));
    await tester.pump(const Duration(milliseconds: 320));
    final call = transport.calls.lastWhere((c) => c.$1 == 'engine.disconnect_channel');
    expect(call.$2['channel'], 'v4');

    expect(find.text('Socket V4 连接'), findsOneWidget, reason: '断一条不该把整页带走');
    expect(find.descendant(of: inCard('v4'), matching: find.text('项圈 · 主')), findsNothing,
        reason: 'v4 的设备该跟着通道消失');
    expect(find.byKey(const ValueKey('channel-device-slot_3')), findsOneWidget,
        reason: '蓝牙通道不该被牵连');
  });

  // 走不走本地中继归设置页管；连接页只有一颗「连接」，按设置选方法。
  testWidgets('本地中继偏好由设置页决定，连接页不再放第二颗开关', (tester) async {
    final (_, transport) = await bootConnect(tester);
    expect(find.text('走本地中继'), findsNothing, reason: '连接页不该再有这条按钮');
    expect(find.byKey(const ValueKey('connect-v4-local')), findsNothing);

    await tester.tap(find.byKey(const ValueKey('connect-v4')));
    await tester.pump(const Duration(milliseconds: 260));
    expect(transport.calls.lastWhere((c) => c.$1 == 'engine.connect_v4').$2['channel'], 'v4');
    expect(transport.calls.any((c) => c.$1 == 'engine.connect_v4_local'), isFalse,
        reason: '设置没开本地中继时不该偷偷起中继');
  });

  // 设置页那颗开关要真的回传新配置，否则连接页永远看不到偏好变了。
  testWidgets('设置页开本地中继后，连接按钮改调本地中继方法', (tester) async {
    final (shell, transport) = await bootConnect(tester);
    await shell.engine.invoke('engine.config_set',
        {'path': 'connection.v4_local', 'value': true});
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('连接（本地中继）'), findsWidgets,
        reason: 'config_set 要回一份新配置，连接页才知道偏好变了');

    await tester.tap(find.byKey(const ValueKey('connect-v4')));
    await tester.pump(const Duration(milliseconds: 260));
    expect(transport.calls.any((c) => c.$1 == 'engine.connect_v4_local'), isTrue,
        reason: '开了本地中继，连接按钮就要走本地中继那条');
  });

  // 二维码栏常驻在卡片右侧：通道一连上就自己取图，不用人去点开折叠块。
  testWidgets('通道一连上后二维码常驻右侧', (tester) async {
    final (_, transport) = await bootConnect(tester);
    await tester.pump(const Duration(milliseconds: 320));

    final card = inCard('v4');
    final pane = find.descendant(of: card, matching: find.text('配对二维码 / 连接信息'));
    expect(pane, findsOneWidget, reason: 'v4 连着就要有常驻的这一栏');
    expect(find.descendant(of: card, matching: find.text('连接地址')), findsOneWidget);
    expect(transport.calls.any((c) => c.$1 == 'engine.qr'), isTrue,
        reason: '通道在线要主动向引擎要一张二维码图');
    expect(find.descendant(of: card, matching: find.byType(Image)), findsOneWidget,
        reason: '拿到 PNG 就要画出来，不能只显示一串链接文本');
    expect(find.descendant(of: card, matching: find.text('studio-mock')), findsOneWidget);
    expect(find.descendant(of: card, matching: find.textContaining('dungeon-lab.cn/s/')),
        findsOneWidget, reason: '配对链接要能复制给手机扫');

    final cardRect = tester.getRect(card);
    final paneX = tester.getTopLeft(pane).dx;
    expect(paneX, greaterThan(cardRect.left + cardRect.width * .55),
        reason: '二维码栏要在卡片右侧，不再插在正文中间');
    // 常驻 = 没有「展开 / 收起」那颗按钮，也没有收起后的占位文案。
    expect(find.byKey(const ValueKey('qr-v4')), findsNothing);
  });

  // 还没生成的通道（v3 未启用）不该凭空摆一张空图。
  testWidgets('没有配对链接的通道不显示二维码块', (tester) async {
    await bootConnect(tester);
    await tester.pump(const Duration(milliseconds: 320));
    expect(find.descendant(of: inCard('v3'), matching: find.text('连接地址')), findsNothing);
    expect(find.descendant(of: inCard('v3'), matching: find.byType(Image)), findsNothing);
    expect(
        find.descendant(of: inCard('v3'), matching: find.textContaining('未启用 · 连接后')),
        findsOneWidget,
        reason: '停用的通道这一栏留空并说明，不显示二维码与地址');
  });

  testWidgets('蓝牙：选中扫描结果与已保存设备后按选中项操作', (tester) async {
    final (_, transport) = await bootConnect(tester);
    expect(find.text('郊狼 · 备'), findsOneWidget, reason: '已保存设备要列出来');

    await tester.tap(find.byKey(const ValueKey('ble-saved-row-AA:BB:CC:DD:EE:01')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('ble-reconnect-saved')));
    await tester.pump(const Duration(milliseconds: 320));
    final call = transport.calls.lastWhere((c) => c.$1 == 'engine.ble_reconnect_saved');
    expect(call.$2['address'], 'AA:BB:CC:DD:EE:01');

    await tester.tap(find.byKey(const ValueKey('ble-forget')));
    await tester.pump(const Duration(milliseconds: 260));
    final forget = transport.calls.lastWhere((c) => c.$1 == 'engine.forget_device');
    expect(forget.$2['address'], 'AA:BB:CC:DD:EE:01');
  });

  testWidgets('自动重连开关写回配置', (tester) async {
    final (_, transport) = await bootConnect(tester);
    await tester.tap(find.byKey(const ValueKey('ble-auto-reconnect')));
    await tester.pump(const Duration(milliseconds: 260));
    final call = transport.calls.lastWhere((c) => c.$1 == 'engine.config_set');
    expect(call.$2['path'], 'auto_reconnect');
    expect(call.$2['value'], false);
  });
}
