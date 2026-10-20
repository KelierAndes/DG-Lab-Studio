import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dgstudio/app/shell.dart';
import 'package:dgstudio/chrome/page_kit.dart';
import 'package:dgstudio/engine/engine_state.dart';
import 'package:dgstudio/engine/engine_transport.dart';
import 'package:dgstudio/pages/app_pages.dart';

import 'harness.dart';

void main() {
  Future<(AppShellState, Finder, Finder)> bootModules(WidgetTester tester, Size size) async {
    final engine = StudioEngine(MockEngineTransport(heartbeat: false));
    addTearDown(engine.dispose);
    final shell = await bootShell<AppShellState>(tester, AppShell(engine: engine), size: size);
    await tester.pump(const Duration(milliseconds: 120));
    shell.nav.go(AppPage.modules);
    await tester.pump(const Duration(milliseconds: 200));
    // 已安装列表的高度是「量完详情再套上去」，要再走一帧才落位。
    await tester.pump();
    final card = find.byType(BentoCard).first;
    return (
      shell,
      card,
      find.ancestor(of: card, matching: find.byType(Scrollable)).first,
    );
  }

  Future<void> hover(WidgetTester tester, Offset at) async {
    await tester.sendEventToBinding(PointerAddedEvent(position: at));
    await tester.sendEventToBinding(PointerHoverEvent(position: at));
    await tester.pump();
  }

  Future<void> wheel(WidgetTester tester, Offset at, double delta, {int times = 1}) async {
    await hover(tester, at);
    for (var i = 0; i < times; i++) {
      await tester.sendEventToBinding(PointerScrollEvent(position: at, scrollDelta: Offset(0, delta)));
      await tester.pump(const Duration(milliseconds: 16));
    }
    await tester.pump(const Duration(milliseconds: 200));
  }

  double pixels(WidgetTester tester, Finder f) => tester.state<ScrollableState>(f).position.pixels;

  // 「已安装」列表对齐右侧详情的高度（用户 2026-10-19 定的方向）：详情定高，
  // 列表超出就在卡内滚，滚轮落在列表上时不该把整页一起带走。
  testWidgets('已安装列表卡内滚动，页面不跟着跑', (tester) async {
    final (_, card, outer) = await bootModules(tester, const Size(1320, 430));
    expect(tester.state<ScrollableState>(outer).position.maxScrollExtent, greaterThan(0),
        reason: '窗口太矮就测不到页面滚动');
    final inner = find.descendant(of: card, matching: find.byType(Scrollable));
    expect(inner, findsOneWidget, reason: '列表比详情高时这张卡要能自己滚');
    expect(tester.getRect(card).height,
        closeTo(tester.getRect(find.byType(BentoCard).at(1)).height, 1),
        reason: '两栏同底：列表卡对齐详情页的高度');

    await wheel(tester, tester.getCenter(card), 120, times: 4);
    expect(pixels(tester, inner.first), greaterThan(0), reason: '滚轮先滚列表');
    expect(pixels(tester, outer), 0, reason: '列表还能滚时不该把页面一起带走');
  });

  // 反过来：滚轮落在卡片外面时页面要照常接手，否则整页滚不动。
  testWidgets('滚轮落在卡片外面时页面照常滚', (tester) async {
    final (_, _, outer) = await bootModules(tester, const Size(1320, 430));
    final first = tester.getRect(find.byType(BentoCard).first);
    // 页面自己的左内边距：在滚动视图里、在所有卡片外。
    // 两张卡片之间那条 12 px 的缝：在页面滚动视图里、在所有卡片外。
    final second = tester.getRect(find.byType(BentoCard).at(1));
    final at = Offset((first.right + second.left) / 2, first.center.dy);
    await wheel(tester, at, 120, times: 6);
    expect(pixels(tester, outer), greaterThan(0), reason: '卡片外面，页面应当接手滚轮');
  });
}
