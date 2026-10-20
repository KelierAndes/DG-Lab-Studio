import 'package:flutter/widgets.dart';

import '../app/tokens.dart';
import 'graph_model.dart';

/// One pin on a Blueprint node. `row` is the vertical slot; left and right
/// columns share the same row index, exactly like Unreal's node layout.
@immutable
class BpPin {
  const BpPin(
    this.id,
    this.label,
    this.kind,
    this.dir,
    this.row, {
    this.valueKey,
  });

  final String id;
  final String label;
  final PortKind kind;
  final PortDir dir;
  final int row;

  /// When set, an unwired input shows an inline default editor bound to this
  /// key in [GraphNode.data] — Unreal's pin-with-default-value widget.
  final String? valueKey;
}

enum BpFieldKind { text, toggle, whole, decimal }

@immutable
class BpField {
  const BpField(this.key, this.label, this.kind, {this.options = const []});

  final String key;
  final String label;
  final BpFieldKind kind;
  final List<String> options;
}

@immutable
class NodeBlueprintDef {
  const NodeBlueprintDef({
    required this.type,
    required this.title,
    required this.category,
    required this.pins,
    this.fields = const [],
    this.width = 190,
    this.subtitleKey,
    this.defaults = const {},
    this.exec = true,
  });

  final String type;
  final String title;
  final BpCategory category;
  final List<BpPin> pins;
  final List<BpField> fields;
  final double width;

  /// Data key whose value is shown as a second line under the title.
  final String? subtitleKey;
  final Map<String, Object?> defaults;

  /// Pure nodes (Get, Make Color, Add) have no execution pins at all.
  final bool exec;

  static const headerH = 30.0;
  static const fieldH = 27.0;
  static const rowH = 19.0;
  static const padTop = 6.0;
  static const padBottom = 10.0;

  int get rowCount => pins.isEmpty
      ? 0
      : pins.map((p) => p.row).reduce((a, b) => a > b ? a : b) + 1;

  double get height =>
      padTop + headerH + fields.length * fieldH + rowCount * rowH + padBottom;

  Size get size => Size(width, height);

  /// Port centres in node-local coordinates; the canvas overlay reads these to
  /// place the interactive sockets, so layout and sockets cannot drift apart.
  List<PortSpec> buildPorts() => [
    for (final p in pins)
      PortSpec(
        id: p.id,
        kind: p.kind,
        dir: p.dir,
        label: p.label,
        dy: padTop + headerH + fields.length * fieldH + p.row * rowH + rowH / 2,
        dx: p.dir == PortDir.output ? -6 : 6,
        valueKey: p.valueKey,
      ),
  ];
}

enum BpCategory {
  flow('流程控制', Color(0xFF3E6C4E)),
  data('数据与运算', Color(0xFF3D5A80)),
  variables('变量', Color(0xFF7A4A2A)),
  output('调试输出', Color(0xFF5A3D7A)),
  annotation('注释与中继', Color(0xFF4A4A52));

  const BpCategory(this.label, this.tint);
  final String label;
  final Color tint;
}

const Map<String, NodeBlueprintDef> blueprintRegistry = {
  'event': NodeBlueprintDef(
    type: 'event',
    title: 'Custom Event',
    category: BpCategory.flow,
    subtitleKey: 'eventName',
    defaults: {'eventName': 'Generate'},
    pins: [BpPin('then', 'then', PortKind.exec, PortDir.output, 0)],
  ),
  'branch': NodeBlueprintDef(
    type: 'branch',
    title: 'Branch',
    category: BpCategory.flow,
    pins: [
      BpPin('exec', 'exec', PortKind.exec, PortDir.input, 0),
      BpPin(
        'condition',
        'Condition',
        PortKind.boolean,
        PortDir.input,
        1,
        valueKey: 'condition',
      ),
      BpPin('true', 'true', PortKind.exec, PortDir.output, 2),
      BpPin('false', 'false', PortKind.exec, PortDir.output, 3),
    ],
  ),
  'sequence': NodeBlueprintDef(
    type: 'sequence',
    title: 'Sequence',
    category: BpCategory.flow,
    pins: [
      BpPin('exec', 'exec', PortKind.exec, PortDir.input, 0),
      BpPin('then0', 'Then 0', PortKind.exec, PortDir.output, 0),
      BpPin('then1', 'Then 1', PortKind.exec, PortDir.output, 1),
      BpPin('then2', 'Then 2', PortKind.exec, PortDir.output, 2),
    ],
  ),
  'forEach': NodeBlueprintDef(
    type: 'forEach',
    title: 'For Each Loop',
    category: BpCategory.flow,
    defaults: {'count': 3},
    fields: [BpField('count', 'Count', BpFieldKind.whole)],
    pins: [
      BpPin('exec', 'exec', PortKind.exec, PortDir.input, 0),
      BpPin('array', 'Array', PortKind.wildcard, PortDir.input, 1),
      BpPin('body', 'Loop Body', PortKind.exec, PortDir.output, 0),
      BpPin('completed', 'Completed', PortKind.exec, PortDir.output, 1),
      BpPin('element', 'Array Element', PortKind.wildcard, PortDir.output, 2),
      BpPin('index', 'Index', PortKind.integer, PortDir.output, 3),
    ],
  ),
  'delay': NodeBlueprintDef(
    type: 'delay',
    title: 'Delay',
    category: BpCategory.flow,
    defaults: {'duration': 0.2},
    pins: [
      BpPin('exec', 'exec', PortKind.exec, PortDir.input, 0),
      BpPin(
        'duration',
        'Duration Time',
        PortKind.floating,
        PortDir.input,
        1,
        valueKey: 'duration',
      ),
      BpPin('then', 'then', PortKind.exec, PortDir.output, 0),
    ],
  ),
  'print': NodeBlueprintDef(
    type: 'print',
    title: 'Print String',
    category: BpCategory.output,
    defaults: {'message': 'Hello from the graph'},
    pins: [
      BpPin('exec', 'exec', PortKind.exec, PortDir.input, 0),
      BpPin('in', 'In', PortKind.string, PortDir.input, 1, valueKey: 'message'),
      BpPin('then', 'then', PortKind.exec, PortDir.output, 0),
    ],
  ),
  'getVar': NodeBlueprintDef(
    type: 'getVar',
    title: 'Get',
    category: BpCategory.variables,
    exec: false,
    subtitleKey: 'varName',
    defaults: {'varName': 'Seed'},
    pins: [BpPin('value', 'Value', PortKind.integer, PortDir.output, 0)],
  ),
  'setVar': NodeBlueprintDef(
    type: 'setVar',
    title: 'Set',
    category: BpCategory.variables,
    subtitleKey: 'varName',
    defaults: {'varName': 'Seed', 'value': 12345},
    pins: [
      BpPin('exec', 'exec', PortKind.exec, PortDir.input, 0),
      BpPin(
        'value',
        'Value',
        PortKind.integer,
        PortDir.input,
        1,
        valueKey: 'value',
      ),
      BpPin('then', 'then', PortKind.exec, PortDir.output, 0),
    ],
  ),
  'makeColor': NodeBlueprintDef(
    type: 'makeColor',
    title: 'Make Color',
    category: BpCategory.data,
    exec: false,
    width: 200,
    defaults: {'r': 0.9, 'g': 0.45, 'b': 0.2, 'a': 1.0},
    pins: [
      BpPin('r', 'R', PortKind.floating, PortDir.input, 0, valueKey: 'r'),
      BpPin('g', 'G', PortKind.floating, PortDir.input, 1, valueKey: 'g'),
      BpPin('b', 'B', PortKind.floating, PortDir.input, 2, valueKey: 'b'),
      BpPin('a', 'A', PortKind.floating, PortDir.input, 3, valueKey: 'a'),
      BpPin('color', 'Color', PortKind.color, PortDir.output, 0),
    ],
  ),
  'addFloat': NodeBlueprintDef(
    type: 'addFloat',
    title: 'Add (float)',
    category: BpCategory.data,
    exec: false,
    width: 176,
    defaults: {'a': 1.0, 'b': 2.0},
    pins: [
      BpPin('a', 'A', PortKind.floating, PortDir.input, 0, valueKey: 'a'),
      BpPin('b', 'B', PortKind.floating, PortDir.input, 1, valueKey: 'b'),
      BpPin('result', 'ReturnValue', PortKind.floating, PortDir.output, 0),
    ],
  ),
  'toString': NodeBlueprintDef(
    type: 'toString',
    title: 'To String',
    category: BpCategory.data,
    exec: false,
    width: 190,
    pins: [
      BpPin('in', 'In', PortKind.wildcard, PortDir.input, 0),
      BpPin('result', 'ReturnValue', PortKind.string, PortDir.output, 0),
    ],
  ),
  'compare': NodeBlueprintDef(
    type: 'compare',
    title: 'Equal (float)',
    category: BpCategory.data,
    exec: false,
    width: 176,
    defaults: {'a': 0.0, 'b': 0.0},
    pins: [
      BpPin('a', 'A', PortKind.floating, PortDir.input, 0, valueKey: 'a'),
      BpPin('b', 'B', PortKind.floating, PortDir.input, 1, valueKey: 'b'),
      BpPin('result', 'ReturnValue', PortKind.boolean, PortDir.output, 0),
    ],
  ),
};

/// What the right-click palette offers, grouped the way Unreal groups them.
const List<BpCategory> bpCategories = [
  BpCategory.flow,
  BpCategory.data,
  BpCategory.variables,
  BpCategory.output,
  BpCategory.annotation,
];

List<String> typesIn(BpCategory category) => blueprintRegistry.entries
    .where((e) => e.value.category == category)
    .map((e) => e.key)
    .toList();

/// Human-friendly menu label for a registry entry.
String bpMenuLabel(String type) {
  final def = blueprintRegistry[type]!;
  return switch (type) {
    'getVar' => 'Get <变量>',
    'setVar' => 'Set <变量>',
    _ => def.title,
  };
}

/// Builds the graph-node payload for a registry entry.
({String title, Size size, List<PortSpec> ports, Map<String, Object?> data})
buildBlueprint(String type, Map<String, Object?> overrides) {
  final def = blueprintRegistry[type]!;
  final data = <String, Object?>{'bp': type, ...def.defaults, ...overrides};
  final title = def.subtitleKey == null
      ? def.title
      : '${def.title} ${(data[def.subtitleKey!] ?? '').toString()}'.trim();
  return (title: title, size: def.size, ports: def.buildPorts(), data: data);
}
