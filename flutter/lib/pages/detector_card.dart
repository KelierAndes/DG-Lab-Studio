import 'dart:async';
import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../chrome/nav_rail.dart';
import '../chrome/primitives.dart';
import '../engine/engine_state.dart';

/// 识别参数：画面识别类模块（vision_link）的实时变量表。
///
/// 参数名即事件流里的变量名，所以这张表写的就是画布的输入。行为四选一，
/// 每种行为要填的字段由引擎给（fields），界面只按类型摆控件；区域和例图
/// 都从「抓一屏 → 框选」来，坐标即模块检测用的图像像素。
class DetectorCard extends StatefulWidget {
  const DetectorCard({super.key, required this.moduleId, required this.engine});

  final String moduleId;
  final StudioEngine engine;

  @override
  State<DetectorCard> createState() => _DetectorCardState();
}

class _DetectorCardState extends State<DetectorCard> {
  Map<String, Object?> _payload = const {};
  bool _loaded = false;
  bool _busy = false;
  Timer? _live;

  @override
  void initState() {
    super.initState();
    unawaited(_pull());
    _live = Timer.periodic(const Duration(milliseconds: 1200), (_) => _pull());
  }

  @override
  void dispose() {
    _live?.cancel();
    super.dispose();
  }

  Future<void> _pull() async {
    if (_busy || !mounted) return;
    final result = await widget.engine.invoke('detectors.state', {
      'module_id': widget.moduleId,
    });
    if (!mounted || result is! Map) return;
    setState(() {
      _payload = Map<String, Object?>.from(result);
      _loaded = true;
    });
  }

  /// 一次编辑 = 一次整包写回 + reload，回包直接当新一帧用，省一次往返。
  Future<void> _send(String method, Map<String, Object?> params) async {
    if (_busy) return;
    await _apply(method, params);
  }

  Future<void> _apply(String method, Map<String, Object?> params) async {
    setState(() => _busy = true);
    final result = await widget.engine.invoke(method, {
      'module_id': widget.moduleId,
      ...params,
    });
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (result is Map) _payload = Map<String, Object?>.from(result);
    });
  }

  List<Map<String, Object?>> get _rows => [
    for (final r in (_payload['rows'] as List? ?? const []))
      if (r is Map) Map<String, Object?>.from(r),
  ];

  List<Map<String, Object?>> get _kinds => [
    for (final r in (_payload['kinds'] as List? ?? const []))
      if (r is Map) Map<String, Object?>.from(r),
  ];

  Map<String, Object?> get _fields =>
      Map<String, Object?>.from(_payload['fields'] as Map? ?? const {});

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    if (!_loaded) {
      return const Padding(
        padding: EdgeInsets.all(12),
        child: Row(
          children: [
            SizedBox(width: 8),
            SizedBox(
              width: 13,
              height: 13,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
            SizedBox(width: 10),
            Expanded(child: Text('正在读取识别参数…')),
          ],
        ),
      );
    }
    if (_payload['supported'] != true) {
      return const EmptyNote('这个模块不负责画面识别，没有识别参数。');
    }
    final rows = _rows;
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 4, 10, 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '每个参数名即一个实时变量，事件流画布用 {名称} 引用。检测颜色/图片/数值输出真·假或数值，'
            '检测数值条输出 0~1。区域坐标是截图图像像素，可手填 x,y,w,h 或点「截区域」框选。',
            style: TextStyle(
              fontSize: 9.5,
              height: 1.35,
              color: t.textTertiary,
            ),
          ),
          const SizedBox(height: 6),
          // 行平铺出来，卡片跟着长：里面再套滚动条就要点两次才能滚到。
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (final row in rows)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: _DetectorRow(
                    key: ValueKey('detector-${row['index']}'),
                    row: row,
                    kinds: _kinds,
                    fields: [
                      for (final f
                          in (_fields['${row['kind']}'] as List? ?? const []))
                        if (f is Map) Map<String, Object?>.from(f),
                    ],
                    busy: _busy,
                    onPatch: (patch) => _send('detectors.update', {
                      'index': row['index'],
                      'patch': patch,
                    }),
                    onPick: (key) => _pick(row, key),
                    onKind: (kind) => _send('detectors.update', {
                      'index': row['index'],
                      'patch': {'kind': kind},
                    }),
                    onRemove: () =>
                        _send('detectors.remove', {'index': row['index']}),
                  ),
                ),
              if (rows.isEmpty) const EmptyNote('还没有识别参数：点下方「添加参数」，先选行为再截取区域。'),
            ],
          ),
          // 添加按钮钉在卡片底部：行数一多，滚动区里的按钮就点不到了。
          GhostButton(
            key: ValueKey('detector-add-${widget.moduleId}'),
            label: '添加参数',
            icon: Icons.add_rounded,
            dense: true,
            onTap: () => _send('detectors.add', const {}),
          ),
        ],
      ),
    );
  }

  /// 截一屏 → 在全屏预览里框选 → 区域写回参数，例图/锚点顺带存进模板目录。
  Future<void> _pick(Map<String, Object?> row, String key) async {
    if (_busy) return;
    // 对话框挂在 Navigator 的 Overlay 里，看不到外壳的 AppScope：先把这一层带过去。
    final tokens = AppScope.of(context);
    final palette = AppScope.colors(context);
    setState(() => _busy = true);
    final shot = await widget.engine.invoke('detectors.shot', {
      'module_id': widget.moduleId,
    });
    if (!mounted) return;
    if (shot is! Map || '${shot['path'] ?? ''}'.isEmpty) {
      setState(() => _busy = false);
      widget.engine.say('截图失败，可先手动填 x,y,w,h');
      return;
    }
    final picked = await showDialog<List<int>>(
      context: context,
      builder: (_) => AppScope(
        tokens: tokens,
        palette: palette,
        child: _ShotDialog(
          path: '${shot['path']}',
          imageWidth: (shot['width'] as num?)?.round() ?? 1,
          imageHeight: (shot['height'] as num?)?.round() ?? 1,
          title: key == 'rect' ? '框选检测区域：拖出矩形后点「用这块」' : '框选例图：拖出矩形后点「用这块」',
        ),
      ),
    );
    if (!mounted) return;
    setState(() => _busy = false);
    if (picked == null) return;
    if (key == 'rect') {
      await _apply('detectors.update', {
        'index': row['index'],
        'patch': {'rect': picked},
        'from_pick': true,
      });
      return;
    }
    await _apply('detectors.crop', {
      'index': row['index'],
      'key': key,
      'rect': picked,
    });
  }
}

class _DetectorRow extends StatelessWidget {
  const _DetectorRow({
    super.key,
    required this.row,
    required this.kinds,
    required this.fields,
    required this.busy,
    required this.onPatch,
    required this.onKind,
    required this.onPick,
    required this.onRemove,
  });

  final Map<String, Object?> row;
  final List<Map<String, Object?>> kinds;
  final List<Map<String, Object?>> fields;
  final bool busy;
  final void Function(Map<String, Object?> patch) onPatch;
  final void Function(String kind) onKind;
  final void Function(String key) onPick;
  final VoidCallback onRemove;

  String get _kind => '${row['kind'] ?? 'color'}';

  Object? _value(String key) {
    final values = row['values'];
    if (values is Map) return values[key];
    return null;
  }

  bool _visible(Map<String, Object?> field) {
    final when = field['when'];
    if (when is! List || when.length != 2) return true;
    return '${when[1]}' == '${_value('${when[0]}')}';
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final rect = row['rect'];
    final rectText = rect is List && rect.length == 4 ? rect.join(',') : '';
    final error = '${row['error'] ?? ''}';
    return WellBox(
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              TinyLabel('参数名', color: t.textTertiary),
              const SizedBox(width: 6),
              CommitField(
                key: ValueKey('detector-name-${row['index']}'),
                text: '${row['name'] ?? ''}',
                hint: '参数名',
                minWidth: 96,
                onSubmit: (v) => onPatch({'name': v}),
              ),
              const SizedBox(width: 8),
              TinyLabel('行为', color: t.textTertiary),
              const SizedBox(width: 6),
              PickerField(
                key: ValueKey('detector-kind-${row['index']}'),
                value: _kindLabel(),
                options: [for (final k in kinds) '${k['label']}'],
                width: 104,
                compact: true,
                onChanged: (label) {
                  for (final k in kinds) {
                    if ('${k['label']}' == label) onKind('${k['key']}');
                  }
                },
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  '实时 ${row['live'] ?? '—'}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontFamily: 'Consolas',
                    fontSize: 11,
                    color: t.textSecondary,
                  ),
                ),
              ),
              const SizedBox(width: 6),
              GhostButton(
                key: ValueKey('detector-remove-${row['index']}'),
                label: '删除',
                dense: true,
                onTap: busy ? () {} : onRemove,
              ),
            ],
          ),
          const SizedBox(height: 7),
          Wrap(
            spacing: 10,
            runSpacing: 7,
            children: [
              _Labeled(
                label: '检测区域',
                child: Row(
                  children: [
                    CommitField(
                      key: ValueKey('detector-rect-${row['index']}'),
                      text: rectText,
                      hint: 'x,y,w,h',
                      minWidth: 104,
                      onSubmit: (v) => onPatch({'rect': v}),
                    ),
                    const SizedBox(width: 4),
                    GhostButton(
                      key: ValueKey('detector-pick-${row['index']}'),
                      label: '截区域',
                      dense: true,
                      onTap: busy ? () {} : () => onPick('rect'),
                    ),
                  ],
                ),
              ),
              for (final field in fields)
                if (_visible(field))
                  _Labeled(label: '${field['label']}', child: _arg(field)),
              if (error.isNotEmpty)
                Text(
                  '⚠ $error',
                  style: TextStyle(
                    fontSize: 9.5,
                    height: 1.2,
                    color: t.portNegative,
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }

  String _kindLabel() {
    for (final k in kinds) {
      if ('${k['key']}' == _kind) return '${k['label']}';
    }
    return _kind;
  }

  Widget _arg(Map<String, Object?> field) {
    final key = '${field['key']}';
    final type = '${field['type']}';
    if (type == 'choice') {
      final options = [
        for (final o in (field['options'] as List? ?? const []))
          if (o is List && o.length == 2) ('${o[0]}', '${o[1]}'),
      ];
      final current = '${_value(key) ?? ''}';
      final shown = options
          .firstWhere((o) => o.$1 == current, orElse: () => (current, current))
          .$2;
      return PickerField(
        key: ValueKey('detector-$key-${row['index']}'),
        value: shown,
        options: [for (final o in options) o.$2],
        width: 84,
        compact: true,
        onChanged: (label) {
          for (final o in options) {
            if (o.$2 == label) {
              onPatch({
                'values': {key: o.$1},
              });
            }
          }
        },
      );
    }
    if (type == 'int' || type == 'percent') {
      final lo = (field['min'] as num?)?.round() ?? 0;
      final hi = (field['max'] as num?)?.round() ?? 999;
      final value = (_value(key) as num?)?.round() ?? lo;
      return StepperField(
        key: ValueKey('detector-$key-${row['index']}'),
        value: value.clamp(lo, hi),
        width: 78,
        // 左右翻手给的是 ±1 增量，敲进去的才是绝对值。
        onStep: (delta) => onPatch({
          'values': {key: (value + delta).clamp(lo, hi)},
        }),
        onCommit: (next) => onPatch({
          'values': {key: next.clamp(lo, hi)},
        }),
      );
    }
    if (type == 'file') {
      final label = key == 'anchor' ? '截锚点' : '截例图';
      return Row(
        children: [
          CommitField(
            key: ValueKey('detector-$key-${row['index']}'),
            text: '${_value(key) ?? ''}',
            hint: '自动生成',
            minWidth: 96,
            onSubmit: (v) => onPatch({
              'values': {key: v},
            }),
          ),
          const SizedBox(width: 4),
          GhostButton(
            key: ValueKey('detector-crop-$key-${row['index']}'),
            label: label,
            dense: true,
            onTap: busy ? () {} : () => onPick(key),
          ),
        ],
      );
    }
    return CommitField(
      key: ValueKey('detector-$key-${row['index']}'),
      text: '${_value(key) ?? ''}',
      hint: key == 'color' ? 'RRGGBB' : '',
      minWidth:
          math.max(70.0, (field['width'] as num?)?.toDouble() ?? 70.0) - 26,
      onSubmit: (v) => onPatch({
        'values': {key: v},
      }),
    );
  }
}

class _Labeled extends StatelessWidget {
  const _Labeled({required this.label, required this.child});

  final String label;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        TinyLabel(label, color: t.textTertiary),
        const SizedBox(height: 3),
        child,
      ],
    );
  }
}

/// 全屏截图上的框选：返回图像像素坐标的 x,y,w,h，取消则返回 null。
class _ShotDialog extends StatefulWidget {
  const _ShotDialog({
    required this.path,
    required this.imageWidth,
    required this.imageHeight,
    required this.title,
  });

  final String path;
  final int imageWidth;
  final int imageHeight;
  final String title;

  @override
  State<_ShotDialog> createState() => _ShotDialogState();
}

class _ShotDialogState extends State<_ShotDialog> {
  Offset? _from;
  Offset? _to;

  // contain 缩放后画面落在盒子哪儿，指针坐标就得往回减多少。
  ({double scale, double dx, double dy}) _layout(BoxConstraints box) {
    final scale = math.min(
      box.maxWidth / widget.imageWidth,
      box.maxHeight / widget.imageHeight,
    );
    return (
      scale: scale <= 0 ? 1 : scale,
      dx: (box.maxWidth - widget.imageWidth * scale) / 2,
      dy: (box.maxHeight - widget.imageHeight * scale) / 2,
    );
  }

  List<int>? _rect(BoxConstraints box) {
    final start = _from;
    final end = _to;
    if (start == null || end == null) return null;
    final l = _layout(box);
    Offset px(Offset at) =>
        Offset(((at.dx - l.dx) / l.scale), ((at.dy - l.dy) / l.scale));
    final a = px(start);
    final b = px(end);
    final x = a.dx.round().clamp(0, widget.imageWidth);
    final y = a.dy.round().clamp(0, widget.imageHeight);
    final w = (b.dx - a.dx).round().clamp(0, widget.imageWidth - x);
    final h = (b.dy - a.dy).round().clamp(0, widget.imageHeight - y);
    if (w < 2 || h < 2) return null;
    return [x, y, w, h];
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Dialog(
      backgroundColor: t.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusCard),
        side: BorderSide(color: t.edge),
      ),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 1080, maxHeight: 660),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: LayoutBuilder(
            builder: (context, box) {
              final l = _layout(box);
              final rect = _rect(box);
              final sel = rect == null
                  ? null
                  : Rect.fromLTWH(
                      l.dx + rect[0] * l.scale,
                      l.dy + rect[1] * l.scale,
                      rect[2] * l.scale,
                      rect[3] * l.scale,
                    );
              return Column(
                children: [
                  Align(
                    alignment: Alignment.centerLeft,
                    child: TinyLabel(
                      widget.title,
                      color: t.textSecondary,
                      size: 11,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Expanded(
                    child: GestureDetector(
                      onPanStart: (d) => setState(() {
                        _from = d.localPosition;
                        _to = d.localPosition;
                      }),
                      onPanUpdate: (d) => setState(() => _to = d.localPosition),
                      child: Container(
                        decoration: BoxDecoration(
                          color: t.wellDeep,
                          borderRadius: BorderRadius.circular(
                            AppTokens.radiusCard,
                          ),
                          border: Border.all(color: t.edge),
                        ),
                        clipBehavior: Clip.antiAlias,
                        child: Stack(
                          children: [
                            Positioned(
                              left: l.dx,
                              top: l.dy,
                              width: widget.imageWidth * l.scale,
                              height: widget.imageHeight * l.scale,
                              child: File(widget.path).existsSync()
                                  ? Image.file(
                                      File(widget.path),
                                      fit: BoxFit.fill,
                                    )
                                  : Center(
                                      child: TinyLabel(
                                        '截图文件读不到',
                                        color: t.textTertiary,
                                      ),
                                    ),
                            ),
                            if (sel != null)
                              Positioned(
                                left: sel.left,
                                top: sel.top,
                                width: sel.width,
                                height: sel.height,
                                child: IgnorePointer(
                                  child: Container(
                                    decoration: BoxDecoration(
                                      color: t.accent.withAlpha(46),
                                      border: Border.all(
                                        color: t.accent,
                                        width: 1.4,
                                      ),
                                    ),
                                  ),
                                ),
                              ),
                          ],
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      TinyLabel(
                        rect == null
                            ? '在截图上拖出一个矩形'
                            : '区域 ${rect.join(',')} （图像像素）',
                        color: t.textTertiary,
                      ),
                      const Spacer(),
                      GhostButton(
                        label: '取消',
                        dense: true,
                        onTap: () => Navigator.of(context).pop(),
                      ),
                      const SizedBox(width: 6),
                      GhostButton(
                        key: const ValueKey('shot-ok'),
                        label: '用这块',
                        dense: true,
                        active: true,
                        onTap: () => Navigator.of(context).pop(rect),
                      ),
                    ],
                  ),
                ],
              );
            },
          ),
        ),
      ),
    );
  }
}
