import 'package:flutter/gestures.dart';
import 'package:flutter/widgets.dart';

/// 把滚轮留在这一块里：里面的列表滚到底之后，不再交给外层页面。
///
/// Flutter 只在「这个 Scrollable 还滚得动」时才登记滚轮事件，所以内层列表一到
/// 边界，同一个滚轮就顺着命中路径交给祖先——用户看到的是「我明明在滚卡片里
/// 那一列，整个页面跟着跑」。这里在卡片这一层补一次登记：命中分发是深层优先，
/// 里面的列表还滚得动时它已经登记过，我这次登记被忽略；它滚不动了才轮到我们，
/// 登记一个空处理，页面就收不到这次滚轮。卡片里根本没有列表时不登记，页面照旧滚。
class IsolatedScroll extends StatefulWidget {
  const IsolatedScroll({super.key, required this.child});

  final Widget child;

  @override
  State<IsolatedScroll> createState() => _IsolatedScrollState();
}

class _IsolatedScrollState extends State<IsolatedScroll> {
  bool _hasScrollable = false;

  void _scan() {
    var found = false;
    void visit(Element e) {
      if (found) return;
      if (e is StatefulElement && e.state is ScrollableState) {
        final position = (e.state as ScrollableState).position;
        if (position.maxScrollExtent > position.minScrollExtent) found = true;
      }
      if (!found) e.visitChildren(visit);
    }

    context.visitChildElements(visit);
    _hasScrollable = found;
  }

  void _onSignal(PointerSignalEvent event) {
    if (event is! PointerScrollEvent) return;
    _scan();
    if (!_hasScrollable) return;
    GestureBinding.instance.pointerSignalResolver.register(event, (_) {});
  }

  @override
  Widget build(BuildContext context) => Listener(
    behavior: HitTestBehavior.translucent,
    onPointerSignal: _onSignal,
    child: widget.child,
  );
}
