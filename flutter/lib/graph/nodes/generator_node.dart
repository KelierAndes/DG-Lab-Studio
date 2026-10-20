import 'dart:math' as math;

import 'package:flutter/widgets.dart';

import '../../app/tokens.dart';
import '../../chrome/primitives.dart';
import '../graph_model.dart';
import '../node_chrome.dart';

const generatorSize = Size(252, 340);

const generatorPorts = <PortSpec>[
  PortSpec(
    id: 'in-model',
    kind: PortKind.model,
    dir: PortDir.input,
    label: 'model',
    dy: 53,
    dx: 6,
  ),
  PortSpec(
    id: 'in-positive',
    kind: PortKind.positive,
    dir: PortDir.input,
    label: 'positive',
    dy: 71,
    dx: 6,
  ),
  PortSpec(
    id: 'in-negative',
    kind: PortKind.negative,
    dir: PortDir.input,
    label: 'negative',
    dy: 89,
    dx: 6,
  ),
  PortSpec(
    id: 'out-image',
    kind: PortKind.image,
    dir: PortDir.output,
    label: 'image',
    dy: 53,
    dx: -6,
  ),
];

const controlModes = <String>['Fixed', 'Safe', 'Adaptive', 'Off'];
const samplers = <String>[
  'dpm++ 2M',
  'dpm++ 2M karras',
  'euler_a',
  'ddim',
  'uni_pc',
];
const strengths = <String>['1.5', '3.0', '4.5', '6.5', '7.5', '9.0', '11.0'];

class GeneratorNodeView extends StatelessWidget {
  const GeneratorNodeView({super.key});

  @override
  Widget build(BuildContext context) {
    final scope = NodeScope.of(context);
    final node = scope.node;
    final t = AppScope.of(context);
    final selected = scope.graph.selection.contains(node.id);

    final seedCtl =
        node.data['seedCtl'] as TextEditingController? ??
        TextEditingController(text: node.read<String>('seed', '12345'));
    node.data['seedCtl'] = seedCtl;

    return DragZone(
      menuBuilder: () => const [
        MenuEntry('选中'),
        MenuEntry('复制节点'),
        MenuEntry('断开全部连线', danger: true),
      ],
      child: NodeCard(
        // Widens with the longest value in the control column.
        width: node.size.width,
        height: generatorSize.height,
        selected: selected,
        padding: EdgeInsets.zero,
        glow: const Color(0x59A78BFA),
        child: Stack(
          children: [
            Positioned.fill(
              child: ClipRRect(
                borderRadius: BorderRadius.circular(AppTokens.radiusNode - 1),
                child: CustomPaint(painter: AuroraPainter(tokens: t)),
              ),
            ),
            Positioned(
              left: 11,
              top: 8,
              right: 11,
              child: const NodeHeader('Image Generator', height: 22),
            ),
            Positioned(
              left: 10,
              top: 34,
              right: 10,
              bottom: 10,
              child: WellBox(
                child: Stack(
                  children: [
                    for (final p in generatorPorts)
                      Positioned(
                        top: p.dy - 43,
                        left: p.dir == PortDir.input ? 4 : null,
                        right: p.dir == PortDir.output ? 4 : null,
                        child: PortRow(spec: p, nodeId: node.id),
                      ),
                    Positioned(
                      left: 0,
                      right: 0,
                      top: 104,
                      bottom: 6,
                      child: Column(
                        children: [
                          const Padding(
                            padding: EdgeInsets.symmetric(horizontal: 10),
                            child: Groove(),
                          ),
                          Expanded(
                            child: _Row(
                              label: 'Randomness',
                              control: Row(
                                children: [
                                  InlineField(
                                    controller: seedCtl,
                                    hint: 'seed',
                                    numeric: true,
                                  ),
                                  const SizedBox(width: 4),
                                  CaretBox(
                                    title: 'Seed',
                                    onTapMenu: (box) => scope.popover.show(
                                      PopoverSpec(
                                        title: 'Seed',
                                        anchorRect: box,
                                        width: 130,
                                        entries: const [
                                          MenuEntry('随机'),
                                          MenuEntry('固定'),
                                          MenuEntry('+1'),
                                          MenuEntry('重置'),
                                        ],
                                        onPick: (i) => _seedAction(
                                          node,
                                          seedCtl,
                                          const ['随机', '固定', '+1', '重置'][i],
                                        ),
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ),
                          Expanded(
                            child: _Row(
                              label: 'Control mode',
                              control: PickerField(
                                value: node.read<String>('control', 'Fixed'),
                                width: 104,
                                title: 'Control mode',
                                options: controlModes,
                                onChanged: (v) => node.set('control', v),
                              ),
                            ),
                          ),
                          Expanded(
                            child: _Row(
                              label: 'Quality steps',
                              control: StepperField(
                                value: node.read<int>('steps', 30),
                                width: 104,
                                onCommit: (v) =>
                                    node.set('steps', v.clamp(1, 150)),
                                onStep: (d) => node.set(
                                  'steps',
                                  (node.read<int>('steps', 30) + d).clamp(
                                    1,
                                    150,
                                  ),
                                ),
                              ),
                            ),
                          ),
                          Expanded(
                            child: _Row(
                              label: 'Prompt strength',
                              control: PickerField(
                                value: node.read<String>('strength', '6.5'),
                                width: 104,
                                title: 'CFG',
                                options: strengths,
                                onChanged: (v) => node.set('strength', v),
                              ),
                            ),
                          ),
                          Expanded(
                            child: _Row(
                              label: 'Sampling method',
                              control: PickerField(
                                value: node.read<String>('sampler', 'dpm++ 2M'),
                                width: 104,
                                title: 'Sampler',
                                options: samplers,
                                onChanged: (v) => node.set('sampler', v),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _seedAction(GraphNode node, TextEditingController ctl, String action) {
    switch (action) {
      case '随机':
        ctl.text = (math.Random().nextInt(900000) + 100000).toString();
        node.set('seed', ctl.text);
      case '+1':
        ctl.text = ((int.tryParse(ctl.text) ?? 0) + 1).toString();
        node.set('seed', ctl.text);
      case '重置':
        ctl.text = '12345';
        node.set('seed', ctl.text);
      default:
        node.set('seed', ctl.text);
    }
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.label, required this.control});

  final String label;
  final Widget control;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 10),
      child: Row(
        children: [
          Expanded(
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 11, height: 1, color: t.textTertiary),
            ),
          ),
          control,
        ],
      ),
    );
  }
}

/// The pink/violet/blue light leak bleeding out of the generator's top-right.
class AuroraPainter extends CustomPainter {
  const AuroraPainter({required this.tokens});

  final AppTokens tokens;

  @override
  void paint(Canvas canvas, Size size) {
    if (!tokens.isDark) {
      canvas.drawRect(Offset.zero & size, Paint()..color = tokens.surface);
      return;
    }
    canvas.drawRect(Offset.zero & size, Paint()..color = tokens.surface);
    void blob(Offset center, double r, Color color) {
      final paint = Paint()
        ..shader = RadialGradient(
          colors: [color, color.withAlpha(0)],
          stops: const [0, 1],
        ).createShader(Rect.fromCircle(center: center, radius: r));
      canvas.drawCircle(center, r, paint);
    }

    blob(
      Offset(size.width * .60, size.height * .010),
      size.width * .34,
      const Color(0x9EFF6EC7),
    );
    blob(
      Offset(size.width * .80, size.height * .035),
      size.width * .30,
      const Color(0x8CA78BFA),
    );
    blob(
      Offset(size.width * .99, size.height * .085),
      size.width * .34,
      const Color(0xB33B82F6),
    );
    blob(
      Offset(size.width * .86, size.height * .150),
      size.width * .26,
      const Color(0x5922D3EE),
    );
  }

  @override
  bool shouldRepaint(AuroraPainter old) => old.tokens != tokens;
}
