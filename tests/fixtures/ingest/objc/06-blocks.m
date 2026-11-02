// Blocks as values and parameters.
#import <Foundation/Foundation.h>

typedef int (^Transformer)(int value);

int applyTransformer(Transformer transformer, int value) { return transformer(value); }

NSArray<NSNumber *> *doubledValues(NSArray<NSNumber *> *values) {
  NSMutableArray<NSNumber *> *out = [NSMutableArray array];
  for (NSNumber *n in values) {
    [out addObject:@([n intValue] * 2)];
  }
  return out;
}
