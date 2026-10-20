import 'package:flutter/widgets.dart';

/// Single source of truth for every colour, radius and shadow in the app.
///
/// The reference mockup is dark-only; [AppTokens.light] is derived from the
/// same slot names so a theme flip never needs hardcoded values anywhere.
@immutable
class AppTokens {
  const AppTokens({
    required this.isDark,
    required this.canvasTop,
    required this.canvasBottom,
    required this.canvasEdge,
    required this.gridDot,
    required this.surface,
    required this.surfaceRaised,
    required this.well,
    required this.wellDeep,
    required this.pill,
    required this.pillBorder,
    required this.hairline,
    required this.embossLight,
    required this.embossDark,
    required this.grooveDark,
    required this.grooveLight,
    required this.textPrimary,
    required this.textSecondary,
    required this.textTertiary,
    required this.textOnAccent,
    required this.edge,
    required this.edgeActive,
    required this.accent,
    required this.portModel,
    required this.portPositive,
    required this.portNegative,
    required this.portImage,
    required this.glassTint,
    required this.glassRim,
    required this.shadowAlpha,
  });

  final bool isDark;

  /// Outer panel gradient + its hairline border.
  final Color canvasTop;
  final Color canvasBottom;
  final Color canvasEdge;
  final Color gridDot;

  /// Node card material.
  final Color surface;
  final Color surfaceRaised;
  final Color well;
  final Color wellDeep;
  final Color pill;
  final Color pillBorder;

  /// Carved-groove / emboss helpers.
  final Color hairline;
  final Color embossLight;
  final Color embossDark;
  final Color grooveDark;
  final Color grooveLight;

  final Color textPrimary;
  final Color textSecondary;
  final Color textTertiary;
  final Color textOnAccent;

  final Color edge;
  final Color edgeActive;
  final Color accent;

  final Color portModel;
  final Color portPositive;
  final Color portNegative;
  final Color portImage;

  final Color glassTint;
  final Color glassRim;
  final double shadowAlpha;

  /// Height of the window's top bar. Everything that hangs off it — the
  /// drawer, the page layer, the share row — is offset from this.
  static const double topBarHeight = 56;

  static const double radiusPanel = 16;
  static const double radiusNode = 13;
  static const double radiusCard = 11;
  static const double radiusField = 9;
  static const double radiusPill = 9;

  static const AppTokens dark = AppTokens(
    isDark: true,
    canvasTop: Color(0xFF0C0C0F),
    canvasBottom: Color(0xFF070708),
    canvasEdge: Color(0x14FFFFFF),
    gridDot: Color(0x12FFFFFF),
    surface: Color(0xFF16161A),
    surfaceRaised: Color(0xFF1C1C21),
    well: Color(0xFF0F0F12),
    wellDeep: Color(0xFF0A0A0C),
    pill: Color(0xFF1A1A1E),
    pillBorder: Color(0x0DFFFFFF),
    hairline: Color(0x0AFFFFFF),
    embossLight: Color(0x0FFFFFFF),
    embossDark: Color(0x66000000),
    grooveDark: Color(0x59000000),
    grooveLight: Color(0x0AFFFFFF),
    textPrimary: Color(0xFFF2F2F5),
    textSecondary: Color(0xFFC4C4CE),
    textTertiary: Color(0xFF8E8E9B),
    textOnAccent: Color(0xFF07110B),
    edge: Color(0x4DFFFFFF),
    edgeActive: Color(0xFF7DD3FC),
    accent: Color(0xFF4ADE80),
    portModel: Color(0xFFF5C542),
    portPositive: Color(0xFF4ADE80),
    portNegative: Color(0xFFFF5A5F),
    portImage: Color(0xFF4DA3FF),
    glassTint: Color(0x33FFFFFF),
    glassRim: Color(0x59FFFFFF),
    shadowAlpha: 0.62,
  );

  static const AppTokens light = AppTokens(
    isDark: false,
    canvasTop: Color(0xFFEFEFF2),
    canvasBottom: Color(0xFFE2E2E7),
    canvasEdge: Color(0x24000000),
    gridDot: Color(0x18000000),
    surface: Color(0xFFFAFAFC),
    surfaceRaised: Color(0xFFFFFFFF),
    well: Color(0xFFE7E7EC),
    wellDeep: Color(0xFFDDDEE3),
    pill: Color(0xFFF4F4F7),
    pillBorder: Color(0x14000000),
    hairline: Color(0x14000000),
    embossLight: Color(0xB3FFFFFF),
    embossDark: Color(0x24000000),
    grooveDark: Color(0x1F000000),
    grooveLight: Color(0x8CFFFFFF),
    textPrimary: Color(0xFF141417),
    textSecondary: Color(0xFF45454F),
    textTertiary: Color(0xFF6C6C78),
    textOnAccent: Color(0xFF06140B),
    edge: Color(0x59000000),
    edgeActive: Color(0xFF0B7FC4),
    accent: Color(0xFF17A34A),
    portModel: Color(0xFFC9930B),
    portPositive: Color(0xFF17A34A),
    portNegative: Color(0xFFD93036),
    portImage: Color(0xFF2A7FD4),
    glassTint: Color(0x8CFFFFFF),
    glassRim: Color(0xCCFFFFFF),
    shadowAlpha: 0.16,
  );

  Color portColor(PortKind kind) => switch (kind) {
    PortKind.model => portModel,
    PortKind.positive => portPositive,
    PortKind.negative => portNegative,
    PortKind.image => portImage,
    _ =>
      (isDark ? ue5PinColors[kind] : ue5PinColorsLight[kind]) ??
          (isDark ? const Color(0xFFB9B9C2) : const Color(0xFF5C5C66)),
  };

  /// Drop shadow used by raised cards. Dark surfaces swallow alpha, so the
  /// blur radius and alpha both scale with [shadowAlpha].
  List<BoxShadow> raised({double depth = 1}) => [
    BoxShadow(
      color: Color.fromRGBO(0, 0, 0, shadowAlpha * 0.55 * depth),
      blurRadius: 18 * depth,
      offset: Offset(0, 6 * depth),
    ),
    BoxShadow(
      color: Color.fromRGBO(0, 0, 0, shadowAlpha * 0.35 * depth),
      blurRadius: 3 * depth,
      offset: Offset(0, 1 * depth),
    ),
  ];

  List<BoxShadow> get inset => [
    BoxShadow(
      color: grooveDark,
      blurRadius: 6,
      offset: const Offset(0, 2),
      spreadRadius: -1,
    ),
    BoxShadow(color: grooveLight, blurRadius: 1, offset: const Offset(0, -1)),
  ];
}

/// Pin data types. The first four belong to the image workflow, the rest are
/// Unreal Blueprint's pin vocabulary — `exec` being the one that changes how a
/// wire is drawn and traversed.
enum PortKind {
  exec,
  model,
  positive,
  negative,
  image,
  boolean,
  integer,
  floating,
  string,
  name,
  text,
  color,
  vector,
  object,
  wildcard,
}

/// Blueprint's pin colours are theme-independent by design; only the light
/// variant is darkened enough to keep contrast on a white node.
const Map<PortKind, Color> ue5PinColors = {
  PortKind.exec: Color(0xFFF2F2F4),
  PortKind.boolean: Color(0xFF59C859),
  PortKind.integer: Color(0xFFD2514B),
  PortKind.floating: Color(0xFF7CC85A),
  PortKind.string: Color(0xFFC8A25A),
  PortKind.name: Color(0xFF5AC8C8),
  PortKind.text: Color(0xFFA8A8B2),
  PortKind.color: Color(0xFFB05AC8),
  PortKind.vector: Color(0xFF5A8DC8),
  PortKind.object: Color(0xFF5AC88F),
  PortKind.wildcard: Color(0xFFB9B9C2),
};

const Map<PortKind, Color> ue5PinColorsLight = {
  PortKind.exec: Color(0xFF2A2A2E),
  PortKind.boolean: Color(0xFF1E7A1E),
  PortKind.integer: Color(0xFFA32A26),
  PortKind.floating: Color(0xFF3F7A1C),
  PortKind.string: Color(0xFF8A6A1E),
  PortKind.name: Color(0xFF1E7A7A),
  PortKind.text: Color(0xFF5C5C66),
  PortKind.color: Color(0xFF7A2A94),
  PortKind.vector: Color(0xFF2A5A94),
  PortKind.object: Color(0xFF1E7A55),
  PortKind.wildcard: Color(0xFF5C5C66),
};

/// Blueprint widens booleans to integers to floats automatically, and a
/// wildcard pin takes anything. Everything else must match exactly.
bool portTypesCompatible(PortKind a, PortKind b) {
  if (a == b) return true;
  if (a == PortKind.wildcard || b == PortKind.wildcard) return true;
  if (a == PortKind.exec || b == PortKind.exec) return false;
  const ladder = [PortKind.boolean, PortKind.integer, PortKind.floating];
  return ladder.contains(a) && ladder.contains(b);
}

String portTypeLabel(PortKind kind) => switch (kind) {
  PortKind.exec => 'exec',
  PortKind.model => 'model',
  PortKind.positive => 'prompt',
  PortKind.negative => 'prompt',
  PortKind.image => 'texture',
  PortKind.boolean => 'bool',
  PortKind.integer => 'integer',
  PortKind.floating => 'float',
  PortKind.string => 'string',
  PortKind.name => 'name',
  PortKind.text => 'text',
  PortKind.color => 'linear color',
  PortKind.vector => 'vector',
  PortKind.object => 'object',
  PortKind.wildcard => 'wildcard',
};

class AppScope extends InheritedWidget {
  const AppScope({
    super.key,
    required this.tokens,
    required this.palette,
    required super.child,
  });

  final AppTokens tokens;
  final List<String> palette;

  static AppTokens of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<AppScope>()!.tokens;

  static List<String> colors(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<AppScope>()!.palette;

  @override
  bool updateShouldNotify(AppScope old) =>
      old.tokens != tokens || old.palette != palette;
}
