import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// Bare test tree that supplies what the real app's root provides: a size, a
/// direction, Material localizations and an [Overlay] — a focused text field
/// installs its selection handles into an Overlay, and only MaterialApp gives
/// one. Plain `Overlay` won't do: `initialEntries` is a one-time seed, so the
/// second `pumpWidget` would keep showing the first child.
class TestHarness extends StatelessWidget {
  const TestHarness({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) => MaterialApp(
        debugShowCheckedModeBanner: false,
        home: child,
      );
}

/// Where the canvas actually sits in window coordinates: canvas-local ("view")
/// coordinates plus this = window coordinates. The drawer moves the canvas, so
/// every boot reads it back off the laid-out widget instead of hardcoding it.
Offset panelOrigin = const Offset(14, 14);

/// Pumps the shell at window size and returns its state.
Future<T> bootShell<T extends State<StatefulWidget>>(WidgetTester tester, Widget app,
    {Size size = const Size(1320, 860)}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(TestHarness(child: const SizedBox.shrink()));
  await tester.pump();
  await tester.pumpWidget(TestHarness(child: app));
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 60));
  // 画布只在事件流页存在，其它页按面板原点计算。
  panelOrigin = find.byKey(const Key('canvas')).evaluate().isNotEmpty
      ? tester.getTopLeft(find.byKey(const Key('canvas')))
      : tester.getTopLeft(find.byKey(const Key('panel')));
  return tester.state<T>(find.byType(app.runtimeType));
}
