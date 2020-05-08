from reinforce import REINFORCE
from sim import MazeSimulator
import matplotlib.pyplot as plt
import random
from copy import deepcopy
from collections import OrderedDict
from tqdm import tqdm
import torch


def update_init_params(target, old, step_size = 0.1):
    """Apply one step of gradient descent on the loss function `loss`, with 
    step-size `step_size`, and returns the updated parameters of the neural 
    network.
    """
    updated = OrderedDict()
    for ((name_old, oldp), (name_target, targetp)) in zip(old.items(), target.items()):
        assert name_old == name_target, "target and old params are different"
        updated[name_old] = oldp + step_size * (targetp - oldp) # grad ascent so its a plus
    return updated

def train_reptile(model, sampler, num_meta_batches, meta_lr = 0.1):
    init_params_policy = deepcopy(OrderedDict(model.policy.named_parameters()))  #TODO: Change to no history? variable history?

    rewards_q_idx = 0
    rewards_q = [-9999999] * 5
    total_rewards = []
    prev_max_score = -9999999

    for meta_i in tqdm(range(0, num_meta_batches)):
        
        # we have to be careful about aliasing here, but since update_init_params
        # returns a new copy, we don't need to deepcopy before entering the params into the model
        model.policy.load_state_dict(init_params_policy)

        # update the policy for 4 steps
        # we reset the optimizer so we don't have to worry about information leakage
        # between batches. See the paper for more details
        model.init_optimizers()

        rewards, losses = model.train(world) # rewards, losses = model.train(world, num_batches=4, batch_size=5, num_mini_batches=1, horizon=100)
        # print("Rewards:", sum(rewards))
        cumulative_reward = sum(rewards)
        total_rewards.append(cumulative_reward)
        rewards_q[rewards_q_idx] = cumulative_reward
        rewards_q_idx = (rewards_q_idx + 1) % 5

        if sum(rewards_q)/len(rewards_q) > prev_max_score:
            prev_max_score = sum(rewards_q)/len(rewards_q)
            print("best average score:", prev_max_score)
            torch.save(model.state_dict(), "meta_train_results/best_meta_init_at_iter%s.pth" %(meta_i))

        # get the policies new parameters
        target_policy = OrderedDict(model.policy.named_parameters())

        init_params_policy = update_init_params(target_policy, init_params_policy, meta_lr)
    
    
    model.policy.load_state_dict(init_params_policy)
    return model, total_rewards

maze = [["W", "W", "W", "W", "W", "W", "W", "W", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", " ", " ", " ", " ", " ", " ", " ", "W"],
        ["W", "W", "W", "W", "W", "W", "W", "W", "W"]]

if __name__ == "__main__":
    '''
    for this distribution training, remember to remember about generate_fresh().
    It uses the settings of the environment that it was generated from!
    '''
    run_reptile = True

    if run_reptile:
        # random seed for better debugging
        random.seed(1)
        world = MazeSimulator(
                        goal_X=6,
                        goal_Y=1,
                        reward_type="distance",
                        state_rep="fullboard",
                        maze=maze,
                        wall_penalty=0,
                        normalize_state=True
                    )
        class Args():   
            def __init__(self, world):
                # type of model related arguments
                self.seed = 1
                self.state_input_size = world.state_size
                self.action_space_size = world.num_actions
                self.lr = 3e-4
                self.ppo = True
                self.ppo_base_epsilon = 0.1
                self.ppo_dec_epsilon = 0.1
                self.use_critic = True
                self.use_entropy = True

                # training related arguments
                self.gradient_clipping = True
                self.random_perm = True
                self.num_batches = 300
                self.num_mini_batches = 2
                self.batch_size = 10
                self.horizon = 100
                self.weight_func = lambda batch_num: (1 - batch_num/self.num_batches)**2

        args = Args(world)
        args.ppo = True
        args.use_critic = True
        args.use_entropy = True
        args.gradient_clipping = False
        args.ppo_base_epsilon = 0.2
        args.ppo_dec_epsilon = 0

        model = REINFORCE(args)

        def sample():
            maze_instance = deepcopy(maze)
            y = random.randint(2, 7)
            x = 4
            for j in range(1, y+1):
                maze_instance[j][x] = "W"
            
            return MazeSimulator(goal_X=x,
                        goal_Y=y,
                        reward_type="distance",
                        state_rep="fullboard",
                        maze=maze_instance,
                        wall_penalty=0,
                        normalize_state=True)

        model_init, rewards = train_reptile(model, sample, 10, meta_lr=0.05)

        # world.visualize(model_init.policy)
        world.visualize_value(model_init.policy, "Valuemap")#.critic)

        plt.plot(list(range(len(rewards))), rewards)
        plt.savefig("RewardsOfReptile")

        torch.save(model.state_dict(), "meta_train_results/final_reptile_model_init.pth")