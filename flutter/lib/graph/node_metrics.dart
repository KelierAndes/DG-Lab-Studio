import 'dart:math' as math;

import '../app/field_metrics.dart';
import 'blueprint.dart';
import 'graph_model.dart';

/// The width a Blueprint card needs so that no pin label, inline value or field
/// is cut off. Rows are mirrored around the centre line, so the card grows by
/// twice the widest half.
double blueprintWidth(NodeBlueprintDef? def, Map<String, Object?> data) {
  if (def == null) return 0;
  var worst = 0.0;
  for (final p in def.buildPorts()) {
    final label = measureText(p.label, pinLabelStyle);
    if (p.dir == PortDir.input) {
      final editable =
          p.valueKey != null && !p.isExec && data.containsKey(p.valueKey!);
      worst = math.max(
        worst,
        label + (editable ? 6 + pinEditorWidth(data[p.valueKey!]) : 0),
      );
    } else {
      worst = math.max(worst, label);
    }
  }
  for (final f in def.fields) {
    worst = math.max(
      worst,
      44 + 6 + math.max(60.0, measureText(displayValue(data[f.key])) + 14),
    );
  }
  worst = math.max(worst, measureText(def.title, cardTitleStyle) + 24);
  // 18 from the edge to the label, 4 past the centre line — mirrored.
  return (worst + 22) * 2;
}

/// The width a card of [kind] needs to show [data] without truncating, floored
/// at the reference width by the caller.
double nodeWidthFor(NodeKind kind, Map<String, Object?> data) {
  switch (kind) {
    case NodeKind.model:
      // The checkpoint picker, plus the 10 card inset and the 8 field inset on
      // each side.
      return pickerWidthFor(displayValue(data['checkpoint'])) + 36;
    case NodeKind.generator:
      final controls = [
        valueBoxWidth(displayValue(data['seed'])) + 4 + fieldHeight,
        pickerWidthFor(displayValue(data['control'])),
        104.0, // the stepper: two 22px buttons and a value box
        pickerWidthFor(displayValue(data['strength'])),
        pickerWidthFor(displayValue(data['sampler'])),
      ];
      var widest = 0.0;
      for (final c in controls) {
        widest = math.max(widest, c);
      }
      // 60 for the label column, 20 for the row insets, 20 for the card insets.
      return widest + 60 + 40;
    case NodeKind.blueprint:
      return blueprintWidth(blueprintRegistry[displayValue(data['bp'])], data);
    case NodeKind.preview:
      // The toolbar strip: 7 + four 26px buttons with 4 gaps + 7, the two
      // pickers with their gaps, the share button and the closing inset.
      return 171 +
          pickerWidthFor(displayValue(data['zoom']), minimum: 46) +
          pickerWidthFor(displayValue(data['format']), minimum: 58);
    case NodeKind.prompt:
    case NodeKind.reroute:
    case NodeKind.comment:
    case NodeKind.flow:
      // 事件流卡片的宽度在建立节点时已按目录算好，这里不再二次测量。
      return 0;
  }
}
