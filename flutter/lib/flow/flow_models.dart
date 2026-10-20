import 'dart:math' as math;

import '../app/field_metrics.dart';
import '../app/tokens.dart';

/// 引擎 `dglab/event_flow.py` 的卡片目录项。画布只按这份描述排版，
/// 连线规则与求值都留在引擎里。
class FlowPin {
  const FlowPin({required this.name, required this.type, this.fallback});

  final String name;
  final String type;
  final Object? fallback;
}

class FlowField {
  const FlowField({
    required this.id,
    required this.label,
    required this.type,
    this.defaultValue,
    this.choices = const [],
    this.labels = const [],
    this.min,
    this.max,
    this.pool = '',
  });

  final String id;
  final String label;
  final String type;
  final Object? defaultValue;
  final List<String> choices;
  final List<String> labels;
  final num? min;
  final num? max;
  final String pool;

  static FlowField fromMap(Map<String, Object?> m) => FlowField(
    id: '${m['id']}',
    label: '${m['label']}',
    type: '${m['type'] ?? 'float'}',
    defaultValue: m['default'],
    choices: [for (final c in (m['choices'] as List? ?? const [])) '$c'],
    labels: [for (final c in (m['labels'] as List? ?? const [])) '$c'],
    min: m['min'] is num ? m['min'] as num : null,
    max: m['max'] is num ? m['max'] as num : null,
    pool: '${m['pool'] ?? ''}',
  );
}

class FlowDef {
  const FlowDef({
    required this.key,
    required this.title,
    required this.category,
    required this.inputs,
    required this.outputs,
    required this.fields,
    this.en = '',
    this.tip = '',
    this.page = 'both',
    this.palette = true,
    this.execIn = false,
    this.entry = false,
    this.sink = false,
    this.op = '',
  });

  final String key;
  final String title;
  final String en;
  final String category;
  final String tip;
  final String page;
  final bool palette;
  final bool execIn;
  final bool entry;

  /// 语义算子（const / add / core_write / branch…）：整理布局按它分列。
  final String op;
  final bool sink;
  final List<FlowPin> inputs;
  final List<FlowPin> outputs;
  final List<FlowField> fields;

  bool get onInputPage => page == 'both' || page == 'input';
  bool get onOutputPage => page == 'both' || page == 'output';

  static FlowDef fromMap(Map<String, Object?> m) => FlowDef(
    key: '${m['key']}',
    title: '${m['title']}',
    en: '${m['en'] ?? ''}',
    category: '${m['cat'] ?? ''}',
    tip: '${m['tip'] ?? ''}',
    page: '${m['page'] ?? 'both'}',
    palette: m['palette'] != false,
    op: '${m['op'] ?? ''}',
    execIn: m['exec_in'] == true,
    entry: m['entry'] == true,
    sink: m['sink'] == true,
    inputs: [for (final p in (m['inputs'] as List? ?? const [])) _pin(p)],
    outputs: [for (final p in (m['outputs'] as List? ?? const [])) _pin(p)],
    fields: [
      for (final f in (m['fields'] as List? ?? const []))
        FlowField.fromMap(Map<String, Object?>.from(f as Map)),
    ],
  );

  static FlowPin _pin(Object? p) {
    final m = Map<String, Object?>.from(p as Map);
    return FlowPin(
      name: '${m['name']}',
      type: '${m['type'] ?? 'float'}',
      fallback: m['default'],
    );
  }
}

/// 画布上的一张卡片（引擎 `FlowNode.to_dict` 的形状）。
class FlowNodeData {
  const FlowNodeData({
    required this.id,
    required this.defKey,
    required this.x,
    required this.y,
    required this.params,
    required this.alias,
    required this.overrides,
  });

  final String id;
  final String defKey;
  final double x;
  final double y;
  final Map<String, Object?> params;
  final String alias;

  /// 未连线输入的临时值：`{引脚下标: 值}`。
  final Map<int, Object?> overrides;

  static FlowNodeData fromMap(Map<String, Object?> m) => FlowNodeData(
    id: '${m['id']}',
    defKey: '${m['def']}',
    x: (m['x'] as num?)?.toDouble() ?? 0,
    y: (m['y'] as num?)?.toDouble() ?? 0,
    params: Map<String, Object?>.from(m['params'] as Map? ?? const {}),
    alias: '${m['alias'] ?? ''}',
    overrides: {
      for (final e in (m['overrides'] as Map? ?? const {}).entries)
        int.tryParse('${e.key}') ?? 0: e.value,
    },
  );
}

class FlowWireData {
  const FlowWireData({
    required this.id,
    required this.srcNode,
    required this.srcIndex,
    required this.dstNode,
    required this.dstIndex,
    required this.type,
  });

  final String id;
  final String srcNode;
  final int srcIndex;
  final String dstNode;
  final int dstIndex;
  final String type;

  static FlowWireData fromMap(Map<String, Object?> m) {
    final src = (m['src'] as List? ?? const ['', 0]).cast<Object>();
    final dst = (m['dst'] as List? ?? const ['', 0]).cast<Object>();
    return FlowWireData(
      id: '${m['id']}',
      srcNode: '${src[0]}',
      srcIndex: (src[1] as num).toInt(),
      dstNode: '${dst[0]}',
      dstIndex: (dst[1] as num).toInt(),
      type: '${m['type'] ?? 'any'}',
    );
  }
}

class FlowState {
  const FlowState({
    required this.active,
    required this.order,
    required this.page,
    required this.nodes,
    required this.wires,
    required this.catalog,
    required this.errors,
    this.idle = const <String>{},
    this.catalogRev = 0,
    this.missingModules = const [],
    this.vars = const {},
  });

  final String active;
  final List<String> order;
  final String page;
  final List<FlowNodeData> nodes;
  final List<FlowWireData> wires;
  final Map<String, FlowDef> catalog;
  final Map<String, String> errors;

  /// 执行流走不到的卡片：引擎每拍重算，界面据此灰显。
  final Set<String> idle;

  /// 引擎那份卡片目录的版本。设备连上后模块才登记出参数，目录会多一批定义；
  /// 这个数一变，界面就得重取整份状态，否则那几张卡永远是只有 key 的空壳。
  final int catalogRev;
  final List<String> missingModules;
  final Map<String, List<Map<String, Object?>>> vars;

  static const empty = FlowState(
    active: '默认',
    order: ['默认'],
    page: 'input',
    nodes: [],
    wires: [],
    catalog: {},
    errors: {},
  );

  FlowState copyWith({
    String? active,
    List<String>? order,
    String? page,
    List<FlowNodeData>? nodes,
    List<FlowWireData>? wires,
    Map<String, FlowDef>? catalog,
    Map<String, String>? errors,
    Set<String>? idle,
    int? catalogRev,
    List<String>? missingModules,
    Map<String, List<Map<String, Object?>>>? vars,
  }) => FlowState(
    active: active ?? this.active,
    order: order ?? this.order,
    page: page ?? this.page,
    nodes: nodes ?? this.nodes,
    wires: wires ?? this.wires,
    catalog: catalog ?? this.catalog,
    errors: errors ?? this.errors,
    idle: idle ?? this.idle,
    catalogRev: catalogRev ?? this.catalogRev,
    missingModules: missingModules ?? this.missingModules,
    vars: vars ?? this.vars,
  );

  FlowDef? def(String key) => catalog[key];

  static FlowState fromMap(Map<String, Object?> m) {
    final profiles = Map<String, Object?>.from(
      m['profiles'] as Map? ?? const {},
    );
    final graph = Map<String, Object?>.from(m['graph'] as Map? ?? const {});
    return FlowState(
      active: '${profiles['active'] ?? '默认'}',
      order: [for (final n in (profiles['order'] as List? ?? const [])) '$n'],
      page: '${m['page'] ?? 'input'}',
      nodes: [
        for (final n in (graph['nodes'] as List? ?? const []))
          FlowNodeData.fromMap(Map<String, Object?>.from(n as Map)),
      ],
      wires: [
        for (final w in (graph['wires'] as List? ?? const []))
          FlowWireData.fromMap(Map<String, Object?>.from(w as Map)),
      ],
      catalog: {
        for (final d in (m['catalog'] as List? ?? const []))
          '${(d as Map)['key']}': FlowDef.fromMap(Map<String, Object?>.from(d)),
      },
      errors: {
        for (final e in (m['errors'] as Map? ?? const {}).entries)
          '${e.key}': '${e.value}',
      },
      idle: {for (final s in (m['idle'] as List? ?? const [])) '$s'},
      catalogRev: (m['catalog_rev'] as num?)?.toInt() ?? 0,
      missingModules: [
        for (final s in (m['missing_modules'] as List? ?? const [])) '$s',
      ],
      vars: {
        'declared': [
          for (final v
              in ((m['vars'] as Map?)?['declared'] as List? ?? const []))
            Map<String, Object?>.from(v as Map),
        ],
        'renamable': [
          for (final v
              in ((m['vars'] as Map?)?['renamable'] as List? ?? const []))
            Map<String, Object?>.from(v as Map),
        ],
      },
    );
  }
}

/// 引擎引脚类型 → 画布端口词汇。
PortKind portKindOf(String type) => switch (type) {
  'exec' => PortKind.exec,
  'int' => PortKind.integer,
  'bool' => PortKind.boolean,
  'str' => PortKind.string,
  'any' => PortKind.wildcard,
  _ => PortKind.floating,
};

/// 画布端口 id 约定：执行入口固定 `exec`，数据引脚按下标。
String inputPortId(int index) => index < 0 ? 'exec' : 'in:$index';
String outputPortId(int index) => 'out:$index';

const double flowRowHeight = 20;
const double flowHeaderHeight = 24;
const double flowMinWidth = 168;

/// 引脚标签前的方向箭头（11 的三角 + 2 的间距），卡片宽度要一起留出来。
const double pinArrowWidth = 13;

/// 一张卡片需要多高：执行入口、数据输入、输出与字段里最高的那一列。
double flowHeightOf(FlowDef? def) {
  if (def == null) return flowHeaderHeight + 20;
  // 引脚占一段行，字段排在引脚下面，互不重叠。
  final pins = math.max(
    (def.execIn ? 1 : 0) + def.inputs.length,
    def.outputs.length,
  );
  return flowHeaderHeight + 10 + (pins + def.fields.length) * flowRowHeight + 10;
}

/// 卡片宽度要容得下最宽的一行：方向箭头 + 引脚标签 + 内联编辑框。
double flowWidthOf(FlowDef? def, FlowNodeData? node) {
  if (def == null) return flowMinWidth;
  var worst = measureText(def.title, cardTitleStyle) + 30;
  // 执行入口的标签不在 inputs 里，它是 portsFor 现造的，宽度得单独算一份。
  if (def.execIn) {
    worst = math.max(worst, measureText('执行', pinLabelStyle) + pinArrowWidth);
  }
  for (var i = 0; i < def.inputs.length; i++) {
    // 数据引脚没有箭头，宽度只算标签。
    final label = measureText(def.inputs[i].name, pinLabelStyle);
    final editable =
        node != null &&
        !node.overrides.containsKey(i) &&
        node.overrides.containsKey(-1) == false;
    worst = math.max(worst, label + (editable ? 96 : 0));
  }
  for (final p in def.outputs) {
    worst = math.max(
      worst,
      measureText(p.name, pinLabelStyle) +
          // 只有执行出口带箭头，数据出口不留那 13 px。
          (p.type == 'exec' ? pinArrowWidth : 0),
    );
  }
  for (final f in def.fields) {
    final value = node?.params[f.id] ?? f.defaultValue;
    final control = f.choices.isNotEmpty
        ? pickerWidthFor(displayValue(value), minimum: 60)
        : switch (f.type) {
            'str' || 'param' => 132.0,
            'int' || 'float' => stepperWidthFor(displayValue(value)),
            _ => valueBoxWidth(displayValue(value)) + 4 + fieldHeight,
          };
    // 参数行是定宽 74 的标签 + 6 的间距 + 控件，两侧还各有 15 的内缩。
    worst = math.max(worst, 30 + 74 + 6 + control);
  }
  return math.max(flowMinWidth, worst + 24);
}

/// 卡片里的数值步进器：两条翻手（20 + 2）夹一个至少 44 宽的值格。
double stepperWidthFor(String value) =>
    44 + math.max(44.0, measureText(value) + 12);
