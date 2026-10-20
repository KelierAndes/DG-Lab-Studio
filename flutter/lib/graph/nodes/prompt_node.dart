import 'package:flutter/material.dart' hide Stack;
import 'package:flutter/widgets.dart';

import '../../app/tokens.dart';
import '../../chrome/primitives.dart';
import '../graph_model.dart';
import '../node_chrome.dart';

const promptSize = Size(236, 356);

const promptPorts = <PortSpec>[
  PortSpec(
    id: 'out-positive',
    kind: PortKind.positive,
    dir: PortDir.output,
    label: 'positive',
    dy: 41,
    dx: -6,
  ),
  PortSpec(
    id: 'out-negative',
    kind: PortKind.negative,
    dir: PortDir.output,
    label: 'negative',
    dy: 219,
    dx: -6,
  ),
];

const _positiveDefault =
    'A black bear with a pink snout, minimalist style, soft gradients, clear blue sky';
const _negativeDefault =
    'No text, unnecessary details, background objects, other animals or people.';

class PromptNodeView extends StatefulWidget {
  const PromptNodeView({super.key});

  @override
  State<PromptNodeView> createState() => _PromptNodeViewState();
}

class _PromptNodeViewState extends State<PromptNodeView> {
  final List<VoidCallback> _bound = [];
  final List<Listenable> _sources = [];

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final node = NodeScope.of(context).node;
    // Created here (not in build) so the listeners below always see them.
    for (final e in {
      'positive': _positiveDefault,
      'negative': _negativeDefault,
    }.entries) {
      node.data.putIfAbsent(e.key, () => TextEditingController(text: e.value));
    }
    for (final key in ['positive', 'negative']) {
      final ctl = node.data[key];
      if (ctl is! Listenable || _sources.contains(ctl)) continue;
      void onChange() => node.touch();
      ctl.addListener(onChange);
      _sources.add(ctl);
      _bound.add(onChange);
    }
  }

  @override
  void dispose() {
    for (var i = 0; i < _sources.length; i++) {
      _sources[i].removeListener(_bound[i]);
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scope = NodeScope.of(context);
    final node = scope.node;
    final t = AppScope.of(context);
    final selected = scope.graph.selection.contains(node.id);

    final pos = node.data['positive'] as TextEditingController;
    final neg = node.data['negative'] as TextEditingController;

    return NodeCard(
      width: promptSize.width,
      height: promptSize.height,
      transparent: true,
      padding: EdgeInsets.zero,
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          Positioned(
            left: 2,
            top: 0,
            width: promptSize.width - 4,
            height: 22,
            child: DragZone(
              menuBuilder: _menu,
              child: Row(
                children: [
                  const Dot(color: Color(0xFFB9B9C2), size: 4.5),
                  const SizedBox(width: 7),
                  Text(
                    'Prompt',
                    style: TextStyle(
                      fontSize: 12,
                      height: 1,
                      color: t.textSecondary,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const Spacer(),
                  const GenerateChip(),
                ],
              ),
            ),
          ),
          Positioned(
            left: 0,
            top: 28,
            child: DragZone(
              menuBuilder: _menu,
              child: _PromptCard(
                nodeId: node.id,
                title: 'Positive',
                accent: t.portPositive,
                caption: pos.text.isEmpty ? _positiveDefault : pos.text,
                field: pos,
                hint: 'Type what you want to get',
                selected: selected,
              ),
            ),
          ),
          Positioned(
            left: 0,
            top: 206,
            child: DragZone(
              menuBuilder: _menu,
              child: _PromptCard(
                nodeId: node.id,
                title: 'Negative',
                accent: t.portNegative,
                caption: neg.text.isEmpty ? _negativeDefault : neg.text,
                field: neg,
                hint: 'Type what do not you want to get',
                selected: selected,
              ),
            ),
          ),
        ],
      ),
    );
  }

  static List<MenuEntry> _menu() => const [
    MenuEntry('选中'),
    MenuEntry('复制节点'),
    MenuEntry('删除', danger: true),
  ];
}

class _PromptCard extends StatelessWidget {
  const _PromptCard({
    required this.nodeId,
    required this.title,
    required this.accent,
    required this.caption,
    required this.field,
    required this.hint,
    required this.selected,
  });

  final String nodeId;
  final String title;
  final Color accent;
  final String caption;
  final TextEditingController field;
  final String hint;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return SizedBox(
      width: promptSize.width,
      height: 150,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: t.surface,
          borderRadius: BorderRadius.circular(AppTokens.radiusCard),
          border: Border.all(
            color: selected ? t.edgeActive.withAlpha(120) : t.hairline,
          ),
          boxShadow: t.raised(depth: .9),
        ),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(13, 6, 20, 12),
          child: Stack(
            children: [
              Positioned(
                left: 0,
                top: 4,
                right: 0,
                child: Row(
                  children: [
                    Dot(color: accent, size: 4.5, glow: true),
                    const SizedBox(width: 7),
                    Text(
                      title,
                      style: TextStyle(
                        fontSize: 11.5,
                        height: 1,
                        color: t.textSecondary,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ),
              ),
              Positioned(
                left: 0,
                top: 34,
                right: 6,
                height: 58,
                child: Text(
                  caption,
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 11,
                    height: 1.55,
                    color: t.textTertiary,
                    letterSpacing: .1,
                  ),
                ),
              ),
              Positioned(
                left: 0,
                right: 6,
                top: 100,
                height: 32,
                child: InlineField(controller: field, hint: hint),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Bright green action chip that sits on the Prompt header.
class GenerateChip extends StatefulWidget {
  const GenerateChip({super.key, this.busy = false});

  final bool busy;

  @override
  State<GenerateChip> createState() => _GenerateChipState();
}

class _GenerateChipState extends State<GenerateChip> {
  bool _hot = false;
  bool _down = false;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hot = true),
      onExit: (_) => setState(() => _hot = false),
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapDown: (_) => setState(() => _down = true),
        onTapUp: (_) => setState(() => _down = false),
        onTapCancel: () => setState(() => _down = false),
        onTap: widget.busy ? null : () {},
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 120),
          height: 22,
          padding: const EdgeInsets.symmetric(horizontal: 9),
          decoration: BoxDecoration(
            color: _down
                ? t.accent.withAlpha(215)
                : (_hot ? Color.lerp(t.accent, Colors.white, .12)! : t.accent),
            borderRadius: BorderRadius.circular(8),
            boxShadow: [
              BoxShadow(
                color: t.accent.withAlpha(_hot ? 120 : 70),
                blurRadius: _hot ? 16 : 10,
                spreadRadius: -2,
              ),
            ],
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.auto_awesome_rounded, size: 10, color: t.textOnAccent),
              const SizedBox(width: 4),
              Text(
                'Generate',
                style: TextStyle(
                  fontSize: 10.5,
                  height: 1,
                  color: t.textOnAccent,
                  fontWeight: FontWeight.w700,
                  letterSpacing: .2,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Keeps a control's taps out of the surrounding [DragZone] arena.
