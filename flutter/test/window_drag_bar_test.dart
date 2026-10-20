import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dgstudio/app/shell.dart';

void main() {
  const channel = MethodChannel('window_manager');
  late List<String> calls;

  setUp(() {
    calls = [];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      calls.add(call.method);
      if (call.method == 'isMaximized' || call.method == 'isFullScreen') return false;
      return null;
    });
  });

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  /// The caption, with a control at its left end and dead space on its right.
  Future<void> pumpCaption(WidgetTester tester) async {
    await tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: Align(
          alignment: Alignment.topLeft,
          child: SizedBox(
            width: 400,
            height: 56,
            child: WindowDragBar(
              child: Row(
                children: [
                  GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onTap: () => calls.add('control tapped'),
                    child: const SizedBox(width: 60, height: 56),
                  ),
                  const Spacer(),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  testWidgets('two quick taps on empty caption space maximise', (tester) async {
    await pumpCaption(tester);
    await tester.tapAt(const Offset(300, 28));
    await tester.pump(const Duration(milliseconds: 60));
    await tester.tapAt(const Offset(300, 28));
    await tester.pumpAndSettle();
    expect(calls, ['isMaximized', 'maximize']);
  });

  testWidgets('one tap alone leaves the window alone', (tester) async {
    await pumpCaption(tester);
    await tester.tapAt(const Offset(300, 28));
    await tester.pumpAndSettle();
    expect(calls, isEmpty);
  });

  testWidgets('the second tap of a maximised window restores it', (tester) async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      calls.add(call.method);
      if (call.method == 'isMaximized') return true;
      if (call.method == 'isFullScreen') return false;
      return null;
    });
    await pumpCaption(tester);
    await tester.tapAt(const Offset(300, 28));
    await tester.pump(const Duration(milliseconds: 60));
    await tester.tapAt(const Offset(300, 28));
    await tester.pumpAndSettle();
    expect(calls, ['isMaximized', 'unmaximize']);
  });

  testWidgets('dragging the caption hands the move to the window manager', (tester) async {
    await pumpCaption(tester);
    final gesture = await tester.startGesture(const Offset(300, 28));
    await gesture.moveBy(const Offset(40, 12));
    await gesture.up();
    await tester.pumpAndSettle();
    expect(calls, contains('startDragging'));
  });

  testWidgets('a control that claims its own box is not held back by the double-tap layer',
      (tester) async {
    await pumpCaption(tester);
    await tester.tapAt(const Offset(30, 28));
    // One frame, no clock advance: a tap that had to wait out the double-tap
    // deadline would still be pending.
    await tester.pump();
    expect(calls, contains('control tapped'));
  });
}
