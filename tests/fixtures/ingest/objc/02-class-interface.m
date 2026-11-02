// @interface with properties and method declarations.
#import <Foundation/Foundation.h>

@interface Point : NSObject
@property(nonatomic) int x;
@property(nonatomic) int y;
- (void)moveByX:(int)dx y:(int)dy;
- (BOOL)isOrigin;
+ (Point *)originPoint;
@end

@implementation Point
- (void)moveByX:(int)dx y:(int)dy {
  self.x += dx;
  self.y += dy;
}

- (BOOL)isOrigin {
  return self.x == 0 && self.y == 0;
}

+ (Point *)originPoint {
  return [[Point alloc] init];
}
@end
