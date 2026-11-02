// Message sends and selectors parking unresolved refs.
#import <Foundation/Foundation.h>

@interface Greeter : NSObject
- (NSString *)greetingFor:(NSString *)name;
@end

NSString *runGreeting(Greeter *greeter, NSString *name) {
  NSString *value = [greeter greetingFor:name];
  return [value stringByAppendingString:@"!"];
}
