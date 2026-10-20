import 'dart:math' as math;

import 'package:flutter/widgets.dart';

/// One field height everywhere: a seed box, a picker, a stepper and a pin
/// editor that share a line must not differ by a pixel.
const double fieldHeight = 30;

/// The style every value inside a card is painted with. The widgets and the
/// measurement below both use it, so a box can never be sized for a different
/// text than the one it renders.
///
/// `letterSpacing` must be pinned: a Text with a null letter spacing inherits
/// one from the ambient default style, and that extra fraction of a pixel per
/// glyph is what makes a measured box come out narrower than the text drawn in
/// it.
const TextStyle fieldValueStyle = TextStyle(
  fontSize: 11.5,
  height: fieldStrutHeight,
  letterSpacing: 0,
);

const TextStyle pinLabelStyle = TextStyle(
  fontSize: 11.5,
  height: 1,
  fontWeight: FontWeight.w600,
  letterSpacing: .1,
);

const TextStyle cardTitleStyle = TextStyle(
  fontSize: 12,
  height: 1,
  fontWeight: FontWeight.w600,
  letterSpacing: .1,
);

/// Width of [text] with [style], on one line.
double measureText(String text, [TextStyle style = fieldValueStyle]) {
  final painter = TextPainter(
    text: TextSpan(text: text, style: style),
    textDirection: TextDirection.ltr,
    maxLines: 1,
  )..layout();
  final w = painter.width;
  painter.dispose();
  return w;
}

/// A "value + caret" picker: 8 inset, the 2px border, the text, the 13 caret,
/// 8 inset, plus slack — the box must never be the thing that clips a value.
double pickerWidthFor(String value, {double minimum = 92}) =>
    math.max(minimum, measureText(value) + 42);

/// A plain value box: 9 + 6 inset, the 2px border, the text, and slack.
double valueBoxWidth(String value, {double minimum = 70}) =>
    math.max(minimum, measureText(value) + 24);

/// The box beside a Blueprint pin. Booleans get the checkbox; everything else
/// is a value field that widens with its number.
double pinEditorWidth(Object? value) {
  if (value is bool) return 16;
  return math.max(34.0, measureText(displayValue(value)) + 18);
}

/// What a field shows for a stored value.
String displayValue(Object? v) {
  if (v is TextEditingController) return v.text;
  if (v == null) return '';
  return '$v';
}

/// Vertical padding that centres a one-line value inside [fieldHeight].
///
/// 单行不再自己算上下留白：那套算法按 10.5 号字算，而框里用的是 [fieldValueStyle]
/// （11.5 / 1.4），两边一错位文字就压在下沿。交给 TextField 的
/// `textAlignVertical` 居中，这里只留左右。
EdgeInsets fieldValuePadding({required int maxLines, double fontSize = 10.5}) {
  if (maxLines > 1) return const EdgeInsets.fromLTRB(9, 7, 6, 7);
  return const EdgeInsets.fromLTRB(9, 0, 6, 0);
}

/// 单行输入框的行盒倍率，全站统一。
///
/// 之前是让 strut 与 style 的 `height` 一一配对，看着严谨，实际把文字压到下沿：
/// `forceStrutHeight` 一旦小于字体的自然行高（微软雅黑约 1.33），Skia 就把整串字形
/// 往下推。成品实测 1.2 的行盒里文字比框心低 3.4 px，1.4 时只剩 0.3 px。
/// 取 1.45 留一点余量，所有字号共用同一套几何，不再逐处配 height。
const double fieldStrutHeight = 1.4;

/// 单行输入框的下沿补偿。
///
/// 成品实测：`textAlignVertical: center` 在这些框里并不成立，文字压在框心下方
/// 约半个字号（11.5 号字低 4.5 px），同一行里的图标却居中——所以不是字体度量，
/// 是装饰层把内容按行盒底对齐。下沿留白是线性的（实测每 1 px 抬约 0.83 px），
/// 按 0.48 倍字号补回去，落到框心 ±1 px 内；随字号缩放，小字号不会补过头。
/// 把单行输入框收紧到正好一行高，居中交给外层。
///
/// 交给 `textAlignVertical` 居中不成立：装饰层把内容按行盒底对齐，成品实测文字比
/// 框心低约半个字号，而同一行里的图标居中正常。留白补偿又随框高变化（26 / 30 / 32
/// 三种框补同一个数会分别落到 -1 / +4 / -5），所以干脆让输入框只占一行高，
/// 由 `Align` 把这一行放到框心——与框多高无关。
const singleLineFieldKey = 'single-line-field';

Widget singleLineBox(
  Widget field, {
  double fontSize = 11.5,
  bool enabled = true,
}) => !enabled
    ? field
    : KeyedSubtree(
        key: const ValueKey(singleLineFieldKey),
        child: Align(
          alignment: Alignment.center,
          // 收紧到一行高之后，三类框（26 / 30 / 32）的残差统一成 +3.5 px：
          // 行盒本身贴底排，得再垫一个下沿把它顶回框心。实测每 1 px 抬 0.83 px，
          // 按 0.6 倍字号垫正好抵消，且随字号缩放。
          child: Padding(
            padding: EdgeInsets.only(bottom: fontSize * .6),
            child: SizedBox(
              height: fontSize * fieldStrutHeight + 1,
              child: field,
            ),
          ),
        ),
      );

/// 单行输入框的正文样式：行高与 [fieldStrut] 必须同一个值。
///
/// `forceStrutHeight` 下，比行盒矮的那一段文字是**贴着行盒底**排的：style 写 1.2、
/// strut 写 1.45，文字就往下掉三四个像素（成品实测搜索框里的字比同行图标低 4.5 px）。
/// 所以字号与行高一起从这一处出，不再让每个框自己写一遍。
TextStyle fieldTextStyle(
  Color color, {
  double fontSize = 11.5,
  String? fontFamily,
  FontWeight? weight,
}) => TextStyle(
  fontSize: fontSize,
  height: fieldStrutHeight,
  letterSpacing: 0,
  fontFamily: fontFamily,
  fontWeight: weight,
  color: color,
);

StrutStyle fieldStrut({double fontSize = 11.5, String? fontFamily}) =>
    StrutStyle(
      fontFamily: fontFamily,
      fontSize: fontSize,
      height: fieldStrutHeight,
      forceStrutHeight: true,
    );

/// 输入框里 placeholder 的样式：必须与同一格的正文同一套字号与行高，否则
/// 空框与填了值的框文字位置不一样，看上去就是「同一个框两种对齐」。
TextStyle fieldHintStyle(
  Color color, {
  double fontSize = 11.5,
  String? fontFamily,
}) => TextStyle(
  fontSize: fontSize,
  height: fieldStrutHeight,
  letterSpacing: 0,
  fontFamily: fontFamily,
  color: color,
);
