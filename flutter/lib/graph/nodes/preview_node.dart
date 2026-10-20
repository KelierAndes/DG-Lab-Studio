import 'package:flutter/material.dart' hide Stack;
import 'package:flutter/widgets.dart';

import '../../app/tokens.dart';
import '../../chrome/primitives.dart';
import '../graph_model.dart';
import '../node_chrome.dart';

const previewSize = Size(288, 400);

const previewPorts = <PortSpec>[
  PortSpec(
    id: 'in-image',
    kind: PortKind.image,
    dir: PortDir.input,
    label: 'image',
    dy: 44,
    dx: 6,
  ),
];

const zoomLevels = <String>['1x', '2x', '3x', '4x'];
const formats = <String>['PNG', 'JPG', 'WEBP'];

class PreviewNodeView extends StatelessWidget {
  const PreviewNodeView({super.key});

  @override
  Widget build(BuildContext context) {
    final scope = NodeScope.of(context);
    final node = scope.node;
    final t = AppScope.of(context);
    final selected = scope.graph.selection.contains(node.id);
    // A preview paints once something is wired into its image port, or when
    // the library dropped a concrete render straight onto it.
    final wired = scope.graph.edges.any((e) => e.to.nodeId == node.id);
    final own = node.data['image'];
    final render = own is String ? own : 'assets/images/render_fox.png';
    final caption = resolveCaption(scope.graph, node.id);

    return NodeCard(
      // The toolbar decides how wide this card has to be.
      width: node.size.width,
      height: previewSize.height,
      transparent: true,
      padding: EdgeInsets.zero,
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          Positioned(
            left: 2,
            top: 0,
            height: 22,
            child: DragZone(
              menuBuilder: _menu,
              child: Row(
                children: [
                  const Dot(color: Color(0xFFB9B9C2), size: 4.5),
                  const SizedBox(width: 7),
                  Text(
                    node.title,
                    style: TextStyle(
                      fontSize: 12,
                      height: 1,
                      color: t.textSecondary,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ],
              ),
            ),
          ),
          Positioned(
            left: 0,
            top: 26,
            width: node.size.width,
            height: 318,
            child: DragZone(
              menuBuilder: _menu,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: t.surface,
                  borderRadius: BorderRadius.circular(AppTokens.radiusNode),
                  border: Border.all(
                    color: selected ? t.edgeActive.withAlpha(140) : t.hairline,
                  ),
                  boxShadow: t.raised(depth: wired ? 1.3 : .8),
                ),
                child: Stack(
                  children: [
                    Positioned(
                      left: 14,
                      top: 9,
                      child: PortRow(
                        spec: previewPorts.first,
                        nodeId: node.id,
                        width: 46,
                      ),
                    ),
                    Positioned(
                      left: 8,
                      right: 8,
                      top: 34,
                      bottom: 8,
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(
                          AppTokens.radiusCard,
                        ),
                        child: wired || own is String
                            ? Stack(
                                fit: StackFit.expand,
                                children: [
                                  Image.asset(render, fit: BoxFit.cover),
                                  DecoratedBox(
                                    decoration: BoxDecoration(
                                      gradient: LinearGradient(
                                        begin: Alignment.center,
                                        end: Alignment.bottomCenter,
                                        colors: [
                                          Colors.transparent,
                                          Colors.black.withAlpha(150),
                                        ],
                                      ),
                                    ),
                                  ),
                                  Positioned(
                                    left: 16,
                                    right: 16,
                                    bottom: 14,
                                    child: Column(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      children: [
                                        Text(
                                          'Final Result',
                                          style: TextStyle(
                                            fontSize: 13.5,
                                            height: 1.2,
                                            color: Colors.white.withAlpha(230),
                                            fontWeight: FontWeight.w600,
                                            letterSpacing: .2,
                                          ),
                                        ),
                                        const SizedBox(height: 6),
                                        Text(
                                          caption,
                                          maxLines: 3,
                                          overflow: TextOverflow.ellipsis,
                                          style: TextStyle(
                                            fontSize: 10.5,
                                            height: 1.5,
                                            color: Colors.white.withAlpha(180),
                                            letterSpacing: .1,
                                          ),
                                        ),
                                      ],
                                    ),
                                  ),
                                ],
                              )
                            : const _EmptyRender(),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
          Positioned(
            left: 0,
            top: 356,
            width: node.size.width,
            child: Align(
              alignment: Alignment.centerLeft,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: t.pill,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: t.pillBorder),
                  boxShadow: t.raised(depth: .7),
                ),
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(7, 7, 7, 7),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      IconSquare(
                        icon: Icons.open_in_full_rounded,
                        size: 26,
                        onTap: () {},
                      ),
                      const SizedBox(width: 4),
                      IconSquare(
                        icon: Icons.grid_view_rounded,
                        size: 26,
                        onTap: () {},
                      ),
                      const SizedBox(width: 4),
                      IconSquare(
                        icon: Icons.history_rounded,
                        size: 26,
                        onTap: () {},
                      ),
                      const SizedBox(width: 4),
                      IconSquare(
                        icon: Icons.download_rounded,
                        size: 26,
                        onTap: () {},
                      ),
                      const SizedBox(width: 7),
                      PickerField(
                        value: node.read<String>('zoom', '2x'),
                        width: 46,
                        compact: true,
                        title: 'Zoom',
                        options: zoomLevels,
                        onChanged: (v) => node.set('zoom', v),
                      ),
                      const SizedBox(width: 4),
                      PickerField(
                        value: node.read<String>('format', 'PNG'),
                        width: 58,
                        compact: true,
                        title: 'Format',
                        options: formats,
                        onChanged: (v) => node.set('format', v),
                      ),
                      const SizedBox(width: 4),
                      IconSquare(
                        icon: Icons.share_outlined,
                        size: 26,
                        onTap: () {},
                      ),
                    ],
                  ),
                ),
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

/// Empty state for a preview whose input port has nothing wired to it.
class _EmptyRender extends StatelessWidget {
  const _EmptyRender();

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: t.wellDeep,
        borderRadius: BorderRadius.circular(AppTokens.radiusCard),
      ),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.image_outlined,
              size: 26,
              color: t.textTertiary.withAlpha(140),
            ),
            const SizedBox(height: 9),
            Text(
              '未连接输出',
              style: TextStyle(
                fontSize: 11,
                height: 1,
                letterSpacing: .6,
                color: t.textTertiary.withAlpha(190),
              ),
            ),
            const SizedBox(height: 5),
            Text(
              '把 Image Generator 的 image 端口连过来',
              style: TextStyle(
                fontSize: 9.5,
                height: 1,
                color: t.textTertiary.withAlpha(120),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Walks the graph backwards from a preview to the prompt that produced it so
/// the caption under the render is real, not decoration.
String resolveCaption(GraphController graph, String previewNodeId) {
  for (final e in graph.edges) {
    if (e.to.nodeId != previewNodeId) continue;
    final src = graph.node(e.from.nodeId);
    if (src == null || src.kind != NodeKind.generator) continue;
    final positive = graph.edges
        .where((x) => x.to.nodeId == src.id && x.to.portId == 'in-positive')
        .firstOrNull;
    if (positive == null) break;
    final promptNode = graph.node(positive.from.nodeId);
    final ctl = promptNode?.data['positive'];
    if (ctl is TextEditingController && ctl.text.trim().isNotEmpty) {
      return ctl.text.trim();
    }
    break;
  }
  return 'Minimalist illustration of a black bear with a pink snout, soft gradients and smooth shapes against a clear blue sky.';
}
