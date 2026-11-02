// Categories extending existing classes.
#import <Foundation/Foundation.h>

@interface NSString (Shouting)
- (NSString *)shouted;
@end

@implementation NSString (Shouting)
- (NSString *)shouted {
  return [self uppercaseString];
}
@end

NSString *Shout(NSString *value) { return [value shouted]; }
