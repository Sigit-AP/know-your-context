// Property attributes and synthesized access.
#import <Foundation/Foundation.h>

@interface Config : NSObject
@property(nonatomic, copy) NSString *name;
@property(nonatomic, readonly) int retries;
@property(nonatomic, weak) id delegate;
- (instancetype)initWithName:(NSString *)name;
@end

@implementation Config
- (instancetype)initWithName:(NSString *)name {
  self = [super init];
  if (self) {
    _name = [name copy];
    _retries = 3;
  }
  return self;
}
@end
