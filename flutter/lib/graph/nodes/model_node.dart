import 'package:flutter/widgets.dart';

import '../../app/tokens.dart';
import '../../chrome/primitives.dart';
import '../graph_model.dart';
import '../node_chrome.dart';

const modelSize = Size(214, 168);

/// [dy] values are node-local so the socket overlay and the label rows land on
/// the same pixel; every card below is laid out with absolute positions for
/// exactly that reason.
const modelPorts = <PortSpec>[
  PortSpec(
    id: 'out-model',
    kind: PortKind.model,
    dir: PortDir.output,
    label: 'model',
    dy: 53,
    dx: -6,
  ),
  PortSpec(
    id: 'out-positive',
    kind: PortKind.positive,
    dir: PortDir.output,
    label: 'positive',
    dy: 71,
    dx: -6,
  ),
  PortSpec(
    id: 'out-negative',
    kind: PortKind.negative,
    dir: PortDir.output,
    label: 'negative',
    dy: 89,
    dx: -6,
  ),
];

const checkpoints = <String>[
  'DreamShaper 6 (SD1.5)',
  'Realistic Vision V5',
  'Anything V5 (Anime)',
  'Protogen XV2.2',
  'OpenJourney 4',
];

class ModelNodeView extends StatelessWidget {
  const ModelNodeView({super.key});

  @override
  Widget build(BuildContext context) {
    final scope = NodeScope.of(context);
    final node = scope.node;
    final t = AppScope.of(context);
    final selected = scope.graph.selection.contains(node.id);
    final current = node.read<String>('checkpoint', checkpoints.first);

    return DragZone(
      menuBuilder: () => const [
        MenuEntry('选中'),
        MenuEntry('复制节点'),
        MenuEntry('删除', danger: true),
      ],
      child: NodeCard(
        // The card is as wide as its longest checkpoint name needs it to be.
        width: node.size.width,
        height: modelSize.height,
        selected: selected,
        glow: selected ? t.portModel : null,
        padding: EdgeInsets.zero,
        child: Stack(
          children: [
            Positioned(
              left: 11,
              top: 8,
              right: 11,
              child: const NodeHeader('Model', height: 22),
            ),
            Positioned(
              left: 10,
              top: 34,
              right: 10,
              bottom: 10,
              child: WellBox(
                child: Stack(
                  clipBehavior: Clip.none,
                  children: [
                    for (final p in modelPorts)
                      Positioned(
                        right: 4,
                        top: p.dy - 43,
                        child: PortRow(spec: p, nodeId: node.id),
                      ),
                    Positioned(
                      left: 4,
                      right: 4,
                      bottom: -6,
                      height: 34,
                      child: IgnorePointer(
                        child: DecoratedBox(
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(
                              AppTokens.radiusCard,
                            ),
                            boxShadow: [
                              BoxShadow(
                                color: t.portModel.withAlpha(
                                  selected
                                      ? (t.isDark ? 130 : 70)
                                      : (t.isDark ? 78 : 40),
                                ),
                                blurRadius: 22,
                                spreadRadius: -4,
                                offset: const Offset(0, 6),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                    Positioned(
                      left: 8,
                      right: 8,
                      bottom: 8,
                      child: PickerField(
                        value: current,
                        width: 174,
                        title: 'Checkpoint',
                        options: checkpoints,
                        onChanged: (v) => node.set('checkpoint', v),
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
}
