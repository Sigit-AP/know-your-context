// Delegate protocol wiring.
#import <Foundation/Foundation.h>

@protocol WorkerDelegate <NSObject>
- (void)workerDidFinish:(id)worker;
@end

@interface Worker : NSObject
@property(nonatomic, weak) id<WorkerDelegate> delegate;
- (void)start;
@end

@implementation Worker
- (void)start {
  [self.delegate workerDidFinish:self];
}
@end
