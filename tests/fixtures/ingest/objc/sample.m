#import <Foundation/Foundation.h>
#import "util.h"

@interface Greeter : NSObject
- (void)greet:(NSString *)name;
@end

@implementation Greeter
- (void)greet:(NSString *)name {
    [self helper:name];
}
- (void)helper:(NSString *)name {
    NSLog(@"%@", name);
}
@end

int helper_fn(int x) {
    return add_one(x);
}
