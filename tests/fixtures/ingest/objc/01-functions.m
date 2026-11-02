// Plain C functions in an Objective-C translation unit.
#import <Foundation/Foundation.h>

static int add(int a, int b) { return a + b; }

int clampValue(int value, int low, int high) {
  if (value < low) return low;
  if (value > high) return high;
  return value;
}

int useAdd(void) { return add(1, 2) + clampValue(5, 0, 10); }
