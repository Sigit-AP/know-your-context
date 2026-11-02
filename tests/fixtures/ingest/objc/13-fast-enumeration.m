// Fast enumeration and literals.
#import <Foundation/Foundation.h>

int totalLength(NSArray<NSString *> *items) {
  int total = 0;
  for (NSString *item in items) {
    total += (int)[item length];
  }
  return total;
}

NSDictionary<NSString *, NSNumber *> *defaultsMap(void) {
  return @{@"retries" : @3, @"timeout" : @30};
}
