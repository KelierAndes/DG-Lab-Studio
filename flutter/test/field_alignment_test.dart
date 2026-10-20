import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dgstudio/app/field_metrics.dart';
import 'package:dgstudio/app/shell.dart';
import 'package:dgstudio/engine/engine_state.dart';
import 'package:dgstudio/engine/engine_transport.dart';
import 'package:dgstudio/pages/app_pages.dart';

import 'harness.dart';

/// 「还有输入框没居中」被反复点名：逐文件用眼睛扫总会漏（扫到过三轮还剩两处），
/// 所以改成把每一页、每个抽屉页签、每个弹层里的单行输入框全部过一遍。
/// 以后新加的框只要没写 textAlignVertical 就直接红。
void main() {
  void sweep(WidgetTester tester, String where) {
    final seen = find.byType(TextField).evaluate();
    expect(seen, isNotEmpty, reason: '$where 一个输入框都没扫到，说明这页没走到位');
    for (final element in seen) {
      final field = element.widget as TextField;
      if ((field.maxLines ?? 1) > 1) continue; // 多行正文贴顶是对的
      expect(field.textAlignVertical, isNotNull,
          reason: '$where 的单行输入框没设 textAlignVertical，文字会顶在上沿');
      expect(field.textAlignVertical!.y, 0.0, reason: '$where 的单行输入框没垂直居中');
      // 行盒倍率全站统一：strut 与 style 的行高各写各的，文字就会贴到框底。
      expect(field.strutStyle?.forceStrutHeight, isTrue,
          reason: '$where 的单行输入框没配 forceStrutHeight strut');
      expect(field.strutStyle?.height, fieldStrutHeight,
          reason: '$where 的 strut 行高必须是统一倍率，不能各写各的');
      final size = field.style?.fontSize;
      // 居中靠外层把输入框收到一行高：装饰层自己会把内容按行盒底对齐，
      // 实测文字比框心低约半个字号，而同一行里的图标居中正常。
      var wrapped = false;
      element.visitAncestorElements((a) {
        final w = a.widget;
        if (w is KeyedSubtree && w.key == const ValueKey(singleLineFieldKey)) {
          wrapped = true;
          return false;
        }
        return true;
      });
      expect(wrapped, isTrue,
          reason: '$where 的单行输入框没套 singleLineBox，文字会压在框底');
      if (size != null) {
        expect(field.strutStyle?.fontSize, size,
            reason: '$where 的 strut 字号要和正文一致，否则行盒按另一套字号算');
        final hint = (field.decoration as InputDecoration).hintStyle;
        if (hint != null && field.decoration!.hintText != null) {
          expect(hint.fontSize, size,
              reason: '$where 的占位文字与正文字号不同：空框和填了值的框会差几像素');
        }
      }
    }
  }

  testWidgets('每一页的每个单行输入框都垂直居中', (tester) async {
    final transport = MockEngineTransport(heartbeat: false);
    final engine = StudioEngine(transport);
    addTearDown(engine.dispose);
    final shell = await bootShell<AppShellState>(tester, AppShell(engine: engine),
        size: const Size(1320, 1500));
    await tester.pump(const Duration(milliseconds: 160));

    for (final page in AppPage.values) {
      shell.nav.go(page);
      await tester.pump(const Duration(milliseconds: 280));
      await tester.pump();
      sweep(tester, page.label);
    }

    // 事件流页的抽屉页签与右键建卡面板各自还带输入框。
    shell.nav.go(AppPage.flow);
    await tester.pump(const Duration(milliseconds: 280));
    sweep(tester, '事件流 · 左栏搜索');

    await tester.tap(find.descendant(
        of: find.byKey(const ValueKey('flow-drawer-tabs')), matching: find.text('变量表')));
    await tester.pump(const Duration(milliseconds: 220));
    sweep(tester, '事件流 · 变量表');

    await tester.tap(find.byKey(const Key('canvas')), buttons: kSecondaryButton);
    await tester.pump(const Duration(milliseconds: 220));
    expect(find.byKey(const ValueKey('palette-search')), findsOneWidget);
    sweep(tester, '事件流 · 建卡搜索面板');
  });
}
