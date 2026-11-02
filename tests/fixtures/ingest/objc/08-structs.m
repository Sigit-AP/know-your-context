// Plain C structs, enums and typedefs beside ObjC code.
#import <Foundation/Foundation.h>

struct Size2D {
  double width;
  double height;
};

enum Level { LevelLow = 1, LevelHigh = 2 };

typedef struct Size2D Size2D;

double areaOf(struct Size2D size) { return size.width * size.height; }
