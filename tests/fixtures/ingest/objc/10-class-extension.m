// Class extensions with private members.
#import <Foundation/Foundation.h>

@interface Counter : NSObject
- (int)value;
- (void)bump;
@end

@interface Counter ()
@property(nonatomic) int inner;
@end

@implementation Counter
- (int)value {
  return self.inner;
}

- (void)bump {
  self.inner += 1;
}
@end
