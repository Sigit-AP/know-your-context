// Protocol declarations and conforming classes.
#import <Foundation/Foundation.h>

@protocol Shape <NSObject>
- (double)area;
@optional
- (NSString *)shapeName;
@end

@interface Circle : NSObject <Shape>
@property(nonatomic) double radius;
@end

@implementation Circle
- (double)area {
  return 3.14159 * self.radius * self.radius;
}
@end
